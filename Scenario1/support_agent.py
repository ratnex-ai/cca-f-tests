"""
CCA-F Scenario 1 - Customer Support Resolution Agent
=============================================================================
Real anthropic SDK. No stubs. This is exam-prep code: it is deliberately
small so every line maps back to one CCA-F idea, not a production system.

    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 support_agent.py

WHAT THIS SCENARIO TESTS (per the CCA-F blueprint)
  Domain 1 - Agentic Architecture & Orchestration (27% of the exam)
      The while-loop below IS "the agentic loop": call the model, see if it
      asked for a tool, run the tool, feed the result back, repeat. The exam
      cares about the RELIABILITY patterns around that loop just as much as
      the loop itself: a turn limit so it can't run forever, guardrails that
      don't depend on the model "deciding" to be safe, and a clear signal for
      when to stop and hand off to a human instead of guessing.
  Domain 2 - Tool Design & MCP Integration (18% of the exam)
      In production, get_customer / lookup_order / process_refund /
      escalate_to_human would be MCP tools exposed by your backend. What the
      exam checks is tool BOUNDARIES: one tool = one job, a description that
      tells the model when (and when NOT) to use it, and business rules
      enforced inside the tool - never left to "the model will behave."
  Domain 5 - Context Management & Reliability (15% of the exam)
      Every support ticket resends the full conversation. Two techniques
      keep that affordable and fast: prompt caching (the system prompt and
      tool list are identical on every ticket, so cache them) and trimming
      old tool output once it's no longer needed, so a long ticket doesn't
      slowly fill the context window with stale data.
=============================================================================
"""

import json
import os

import anthropic

MODEL = "claude-opus-5"
MAX_TURNS = 6                        # Domain 1: hard cap so a confused agent can't loop forever
REFUND_AUTO_APPROVE_LIMIT_CENTS = 10_000   # $100 - above this a human must approve
# PYTHON NOTE: 10_000 == 10000. The underscore is just a readability separator.


# ---------------------------------------------------------------------------
# Fake backend. A real agent would reach these through MCP tool calls into
# your actual customer/order database - the tool functions below are the
# same shape either way, only the storage changes.
# ---------------------------------------------------------------------------
CUSTOMERS = {
    "CUS-1001": {"name": "Asha Verma", "email": "asha@example.com", "tier": "standard"},
    "CUS-1002": {"name": "Diego Ruiz", "email": "diego@example.com", "tier": "premium"},
}

ORDERS = {
    "ORD-5001": {"customer_id": "CUS-1001", "status": "delivered", "total_cents": 4_200, "refunded_cents": 0},
    "ORD-5002": {"customer_id": "CUS-1002", "status": "delivered", "total_cents": 129_900, "refunded_cents": 0},
}


# ---------------------------------------------------------------------------
# TOOLS (Domain 2). Each function does exactly one thing and returns a plain
# dict - the loop below turns that dict into JSON for the tool_result block.
# PYTHON NOTE: type hints like `str` and `-> dict` don't change runtime
# behaviour; they're documentation the reader (and some IDEs) can check.
# ---------------------------------------------------------------------------
def get_customer(customer_id: str) -> dict:
    """Look up a customer profile. Nothing else - not orders, not refunds."""
    customer = CUSTOMERS.get(customer_id)
    if customer is None:
        return {"error": f"no customer found with id {customer_id}"}
    return {"customer_id": customer_id, **customer}


def lookup_order(order_id: str) -> dict:
    """Look up one order. Use after the customer is identified."""
    order = ORDERS.get(order_id)
    if order is None:
        return {"error": f"no order found with id {order_id}"}
    return {"order_id": order_id, **order}


def process_refund(order_id: str, amount_cents: int, reason: str) -> dict:
    """Refund part or all of an order - but only within policy.

    Domain 1 idea: the $100 limit is enforced HERE, in code, not just stated
    in the system prompt. A prompt instruction is a request; a check inside
    the tool is a guarantee. Never trust the model alone to police a policy
    that has real financial consequences.
    """
    order = ORDERS.get(order_id)
    if order is None:
        return {"error": f"no order found with id {order_id}"}

    remaining = order["total_cents"] - order["refunded_cents"]
    if amount_cents > remaining:
        return {"error": f"refund of {amount_cents} exceeds remaining refundable amount {remaining}"}

    if amount_cents > REFUND_AUTO_APPROVE_LIMIT_CENTS:
        return {
            "error": "refund_requires_human_approval",
            "message": (
                f"Refunds over ${REFUND_AUTO_APPROVE_LIMIT_CENTS / 100:.2f} need a human. "
                "Call escalate_to_human instead of retrying this tool."
            ),
        }

    order["refunded_cents"] += amount_cents
    return {"status": "refunded", "order_id": order_id, "amount_cents": amount_cents, "reason": reason}


def escalate_to_human(reason: str, summary: str) -> dict:
    """Hand the ticket to a human agent. Call this instead of guessing when
    the request is out of policy, the customer is upset, or a tool above
    just said it needs human approval."""
    return {"status": "escalated", "reason": reason, "summary": summary}


# PYTHON NOTE: a dict of functions is a simple "dispatch table" - instead of
# a long if/elif chain, TOOL_FUNCTIONS[name] looks up the right function.
TOOL_FUNCTIONS = {
    "get_customer": get_customer,
    "lookup_order": lookup_order,
    "process_refund": process_refund,
    "escalate_to_human": escalate_to_human,
}

# The JSON schemas sent to the API. The description is written FOR THE
# MODEL - it decides when a tool gets called, so state what the tool does,
# when to use it, and when not to (Domain 2 tool-design guidance).
TOOLS = [
    {
        "name": "get_customer",
        "description": (
            "Retrieve a customer's profile (name, email, membership tier) by "
            "customer_id. Use this first, to confirm who you're talking to. "
            "Do not use it to look up orders."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "lookup_order",
        "description": (
            "Retrieve one order's status, total, and how much has already "
            "been refunded, by order_id. Use this before touching a refund "
            "so you know the remaining refundable amount."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "process_refund",
        "description": (
            f"Refund an order, up to ${REFUND_AUTO_APPROVE_LIMIT_CENTS / 100:.2f} without "
            "human approval. If it returns refund_requires_human_approval, do "
            "not retry it - call escalate_to_human instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "amount_cents": {"type": "integer", "minimum": 1},
                "reason": {"type": "string"},
            },
            "required": ["order_id", "amount_cents", "reason"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "End your involvement and route the ticket to a human agent. Use "
            "this for anything outside policy, an upset customer, or when "
            "another tool told you approval is required. This is a good "
            "outcome, not a failure - guessing on a request you can't safely "
            "resolve is the real failure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "summary": {"type": "string", "description": "1-2 sentences a human can act on immediately."},
            },
            "required": ["reason", "summary"],
        },
    },
]

SYSTEM_PROMPT = (
    "You are a customer support agent for an online store. Always verify the "
    "customer with get_customer and the relevant order with lookup_order "
    "before taking any action. Refunds up to the policy limit can be applied "
    "directly with process_refund. If a tool reports that human approval is "
    "required, or the request is ambiguous, or the customer is frustrated, "
    "call escalate_to_human rather than guessing. Keep replies short and "
    "plain - this is a chat, not an email."
)

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY (or an `ant auth login` profile)


def trim_old_tool_results(messages: list) -> list:
    """Domain 5: context management. Once a tool result has been used for a
    couple of turns, the model rarely needs its full text again - keeping it
    around just spends tokens on every future request. This replaces
    tool_result content older than the last 2 exchanges with a short marker.
    A real system would reach for the API's built-in compaction/context-
    editing features on long conversations; this is the same idea by hand.
    """
    cutoff = len(messages) - 4  # keep the most recent exchange in full
    for i, msg in enumerate(messages):
        if i >= cutoff:
            continue
        if msg["role"] != "user" or not isinstance(msg["content"], list):
            continue
        for block in msg["content"]:
            if block.get("type") == "tool_result":
                block["content"] = "[older tool result trimmed to save context]"
    return messages


def run_ticket(user_message: str) -> dict:
    """The agentic loop (Domain 1): ask the model, run whatever tool it
    asked for, feed the result back, repeat - until it answers in plain
    text or MAX_TURNS forces a stop.
    """
    messages = [{"role": "user", "content": user_message}]
    escalated = False

    for turn in range(1, MAX_TURNS + 1):
        messages = trim_old_tool_results(messages)

        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            # Domain 5: prompt caching. system + tools are identical on every
            # ticket - this line tells the API to cache that prefix so later
            # tickets pay a fraction of the input-token cost for it.
            cache_control={"type": "ephemeral"},
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            final_text = next((b.text for b in response.content if b.type == "text"), "")
            return {"resolved": not escalated, "escalated": escalated, "reply": final_text, "turns": turn}

        # PYTHON NOTE: a list comprehension with a condition - build a list of
        # only the tool_use blocks, skipping any text blocks in the same turn.
        tool_calls = [b for b in response.content if b.type == "tool_use"]

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for call in tool_calls:
            func = TOOL_FUNCTIONS[call.name]
            result = func(**call.input)
            if call.name == "escalate_to_human":
                escalated = True
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": call.id,   # must match the tool_use block it answers
                "content": json.dumps(result),
            })
        messages.append({"role": "user", "content": tool_results})

    # Domain 1 reliability: hitting the turn cap means the agent is stuck,
    # not that it's fine to keep trying. Treat it as an escalation.
    return {"resolved": False, "escalated": True, "reply": "Reached the turn limit without resolving.", "turns": MAX_TURNS}


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY first: export ANTHROPIC_API_KEY=sk-ant-...")
        return

    tickets = [
        "Hi, I'm Asha Verma (CUS-1001). My order ORD-5001 arrived damaged, "
        "can I get a refund?",
        "This is Diego Ruiz, CUS-1002. Order ORD-5002 never arrived and I "
        "want the full $1299 back right now, this is unacceptable.",
    ]

    resolved_count = 0
    for i, ticket in enumerate(tickets, start=1):
        print(f"\n--- Ticket {i} ---\n{ticket}")
        try:
            outcome = run_ticket(ticket)
        except anthropic.AuthenticationError:
            print("Invalid or missing API key.")
            return
        except anthropic.RateLimitError:
            print("Rate limited - try again shortly.")
            continue
        except anthropic.APIStatusError as e:
            print(f"API error ({e.status_code}): {e.message}")
            continue

        print(f"-> {'ESCALATED' if outcome['escalated'] else 'RESOLVED'} "
              f"in {outcome['turns']} turn(s): {outcome['reply']}")
        resolved_count += not outcome["escalated"]

    # Domain 1 KPI from the scenario brief: 80%+ first-contact resolution.
    rate = resolved_count / len(tickets) * 100
    print(f"\nFirst-contact resolution: {rate:.0f}% (target: 80%+)")


if __name__ == "__main__":
    main()
