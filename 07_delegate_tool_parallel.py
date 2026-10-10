"""
07 - CUSTOMER SUPPORT RESOLUTION: COORDINATOR + SUBAGENTS  (CCA-F Domain 1.2)
=============================================================================
SCENARIO: a customer support resolution agent handles high-ambiguity
requests (returns, billing disputes, account issues) through backend MCP
tools: get_customer, lookup_order, process_refund, escalate_to_human.
Target: >= 80% first-contact resolution (FCR), with RELIABLE escalation to a
human when a case falls outside what the agent should handle.

Scenario1/support_agent.py solves this with ONE agent. This file shows the
coordinator -> subagents version, built at two layers:

  PART A - Messages API: YOU build the mechanism. The coordinator gets a
           hand-built `delegate` tool (a Task tool) plus escalate_to_human.
           Several delegate calls in ONE response run in PARALLEL
           (asyncio.gather). Each subagent gets only its own backend tool.
  PART B - Agent SDK: the same four tools as a real in-process MCP server
           (create_sdk_mcp_server), subagents as AgentDefinitions scoped to
           mcp__support__* tools, a PreToolUse hook as the refund gate, and
           max_turns / max_budget_usd enforced by the SDK.

    pip install anthropic claude-agent-sdk
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 07_delegate_tool_parallel.py          # runs both parts
    python3 07_delegate_tool_parallel.py a        # PART A only
    python3 07_delegate_tool_parallel.py b        # PART B only

ROLES
-----------------------------------------------------------------------------
  coordinator         talks to the customer, plans, decides, escalates.
                      Tools: delegate (PART A) / Agent (PART B),
                      escalate_to_human. Never touches backend data directly.
  account-verifier    get_customer only     (read-only)
  order-investigator  lookup_order only     (read-only)
  refund-processor    process_refund only   (the ONLY write path)

WHAT THE EXAM TESTS IN THIS SCENARIO (and where it is handled below)
-----------------------------------------------------------------------------
* Business rules live in CODE, not the prompt: the refund limit and the
  "verify identity before refunding" prerequisite are enforced inside
  process_refund (both parts) and again by a PreToolUse hook (PART B).
  A prompt rule like "never refund over $100" is a suggestion; this is a gate.
* Escalate for the RIGHT reasons only: the customer explicitly asks for a
  human, the case needs a policy exception, or the agent cannot make
  progress. Not frustration alone, not the model's self-rated confidence.
                                                    -> COORDINATOR_PROMPT
* escalate_to_human is on the COORDINATOR, so escalation is always reachable
  and doesn't depend on a subagent deciding to do it.
* The human gets a STRUCTURED handoff (customer, issue, actions taken,
  recommended action) - they have no access to the conversation.
                                                    -> TOOL_SPECS escalate
* Tool errors are structured (category, retryable or not), so the agent
  knows to escalate rather than retry a business-rule rejection.
* Least privilege: only refund-processor can move money; the read-only
  subagents cannot, and the coordinator can't call backend tools at all.
* Multi-issue tickets: independent lookups fan out in parallel.
* FCR is measured together with escalation correctness - suppressing
  escalations to hit 80% is the trap, not the goal.   -> run_tickets()
=============================================================================
"""

import asyncio
import copy
import json
import os
import sys

import anthropic

client = anthropic.AsyncAnthropic(max_retries=3, timeout=120.0)

COORDINATOR_MODEL = "claude-sonnet-5-5"
SUBAGENT_MODEL = "claude-haiku-4-5-20251001"    # narrow lookups: cheap model

REFUND_AUTO_APPROVE_LIMIT_CENTS = 10_000        # $100 - above this, a human decides


# =============================================================================
# SHARED - FAKE BACKEND (in production: your systems behind an MCP server)
# =============================================================================
CUSTOMERS = {
    "CUS-1001": {"name": "Asha Verma", "email": "asha@example.com", "tier": "standard"},
    "CUS-1002": {"name": "Diego Ruiz", "email": "diego@example.com", "tier": "premium"},
}
INITIAL_ORDERS = {
    "ORD-5001": {"customer_id": "CUS-1001", "status": "delivered",
                 "total_cents": 4_200, "refunded_cents": 0, "note": "reported damaged"},
    "ORD-5003": {"customer_id": "CUS-1001", "status": "delivered",
                 "total_cents": 2_599, "refunded_cents": 0, "note": "charged twice"},
    "ORD-5002": {"customer_id": "CUS-1002", "status": "lost_in_transit",
                 "total_cents": 129_900, "refunded_cents": 0, "note": ""},
}

# Per-ticket state. Reset before each ticket so tickets don't leak into
# each other.
ORDERS: dict = {}
VERIFIED: set = set()       # customer_ids whose identity was verified
ESCALATIONS: list = []      # handoffs created during the current ticket


def reset_backend() -> None:
    ORDERS.clear()
    ORDERS.update(copy.deepcopy(INITIAL_ORDERS))
    VERIFIED.clear()
    ESCALATIONS.clear()


def tool_error(category: str, retryable: bool, message: str) -> dict:
    # Structured error: tells the agent WHAT went wrong and WHETHER retrying
    # can help. "Operation failed" would leave it guessing.
    return {"isError": True, "errorCategory": category,
            "isRetryable": retryable, "message": message}


def get_customer(customer_id: str, email: str) -> dict:
    customer = CUSTOMERS.get(customer_id)
    if customer is None:
        return tool_error("not_found", False, f"No customer {customer_id}.")
    if customer["email"].lower() != email.lower():
        return tool_error("verification_failed", False,
                          "Email does not match this customer. Do not proceed "
                          "with account changes or refunds.")
    VERIFIED.add(customer_id)
    return {"customer_id": customer_id, "verified": True, **customer}


def lookup_order(order_id: str) -> dict:
    order = ORDERS.get(order_id)
    if order is None:
        return tool_error("not_found", False, f"No order {order_id}.")
    return {"order_id": order_id, **order}


def process_refund(order_id: str, amount_cents: int, reason: str) -> dict:
    order = ORDERS.get(order_id)
    if order is None:
        return tool_error("not_found", False, f"No order {order_id}.")
    # Prerequisite gate in code: no refund until the owner was verified.
    if order["customer_id"] not in VERIFIED:
        return tool_error("prerequisite_missing", False,
                          "Customer identity not verified. Verify with "
                          "get_customer first.")
    remaining = order["total_cents"] - order["refunded_cents"]
    if amount_cents <= 0 or amount_cents > remaining:
        return tool_error("validation", False,
                          f"Amount must be between 1 and {remaining} cents.")
    # Business rule in code: the model cannot talk its way past this.
    if amount_cents > REFUND_AUTO_APPROVE_LIMIT_CENTS:
        return tool_error("business_rule", False,
                          f"Refunds over {REFUND_AUTO_APPROVE_LIMIT_CENTS} "
                          "cents need human approval. Escalate; do not retry "
                          "or split into smaller refunds.")
    order["refunded_cents"] += amount_cents
    return {"refund_id": f"RF-{order_id}-{order['refunded_cents']}",
            "order_id": order_id, "amount_cents": amount_cents,
            "status": "refunded"}


def escalate_to_human(customer_id: str, reason: str, issue_summary: str,
                      actions_taken: str, recommended_action: str) -> dict:
    handoff = {"customer_id": customer_id, "reason": reason,
               "issue_summary": issue_summary, "actions_taken": actions_taken,
               "recommended_action": recommended_action}
    ESCALATIONS.append(handoff)
    return {"status": "escalated", "ticket_id": f"HUM-{len(ESCALATIONS):03d}",
            "message": "A human agent will contact the customer within 24h."}


BACKEND = {"get_customer": get_customer, "lookup_order": lookup_order,
           "process_refund": process_refund,
           "escalate_to_human": escalate_to_human}


# =============================================================================
# SHARED - TOOL SPECS (name / description / input_schema) used by BOTH parts
# =============================================================================
# The descriptions carry the "when to use / when NOT to" guidance; the
# schemas constrain the arguments. Same specs become Messages API tools
# (PART A) and MCP tools (PART B).
TOOL_SPECS = {
    "get_customer": {
        "description": (
            "Verify a customer's identity and return their profile. Requires "
            "BOTH the customer id and the email on file. Must succeed before "
            "any refund for that customer. Returns errorCategory "
            "'verification_failed' if the email does not match - do not "
            "proceed in that case."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "pattern": "^CUS-[0-9]+$",
                                "description": "Customer id, e.g. CUS-1001."},
                "email": {"type": "string",
                          "description": "Email address the customer gave."},
            },
            "required": ["customer_id", "email"],
            "additionalProperties": False,
        },
    },
    "lookup_order": {
        "description": (
            "Return one order's status, total, amount already refunded, and "
            "owning customer id. Read-only. Use to check facts before any "
            "refund decision. One order per call - call it once per order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "pattern": "^ORD-[0-9]+$",
                             "description": "Order id, e.g. ORD-5001."},
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
    },
    "process_refund": {
        "description": (
            "Refund part or all of ONE order to the original payment method. "
            "Only for a verified customer's own order, within policy. Refunds "
            f"over {REFUND_AUTO_APPROVE_LIMIT_CENTS} cents are rejected with "
            "errorCategory 'business_rule' - that means escalate, never retry "
            "or split the amount."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "pattern": "^ORD-[0-9]+$"},
                "amount_cents": {"type": "integer", "minimum": 1,
                                 "description": "Amount in cents."},
                "reason": {"type": "string",
                           "enum": ["damaged", "not_received",
                                    "duplicate_charge", "other"]},
            },
            "required": ["order_id", "amount_cents", "reason"],
            "additionalProperties": False,
        },
    },
    "escalate_to_human": {
        "description": (
            "Hand the case to a human agent. Use ONLY when: the customer "
            "explicitly asks for a human; the case needs a policy exception "
            "(e.g. refund over the limit); or you cannot make progress after "
            "investigating. The human has NO access to this conversation, so "
            "every field must stand on its own."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string",
                                "description": "Customer id, or 'unknown'."},
                "reason": {"type": "string",
                           "enum": ["customer_requested_human",
                                    "policy_exception", "unable_to_resolve"]},
                "issue_summary": {"type": "string",
                                  "description": "What the customer needs, "
                                                 "with order ids and amounts."},
                "actions_taken": {"type": "string",
                                  "description": "What was verified, looked "
                                                 "up or refunded already."},
                "recommended_action": {"type": "string",
                                       "description": "What the human should "
                                                      "do next."},
            },
            "required": ["customer_id", "reason", "issue_summary",
                         "actions_taken", "recommended_action"],
            "additionalProperties": False,
        },
    },
}


# =============================================================================
# SHARED - SUBAGENTS AND COORDINATOR POLICY
# =============================================================================
# Each subagent owns exactly one backend tool: least privilege per role.
FACTS_CONTRACT = (
    "\n\nReturn ONLY a JSON object, no prose: "
    '{"result": <tool output or summary>, "ok": true|false, '
    '"error": null | {"errorCategory": "...", "isRetryable": bool, '
    '"message": "..."}}. Report tool errors exactly; never work around them.'
)

SUBAGENTS = {
    "account-verifier": {
        "description": "Verifies a customer's identity (id + email) and "
                       "returns their profile. Read-only.",
        "prompt": "You verify customer identity with get_customer. You have "
                  "no other tools and make no decisions." + FACTS_CONTRACT,
        "tools": ["get_customer"],
    },
    "order-investigator": {
        "description": "Looks up ONE order's facts (status, total, refunds, "
                       "owner). Read-only. Spawn one per order.",
        "prompt": "You look up the order in your brief with lookup_order "
                  "and report the facts. You make no decisions."
                  + FACTS_CONTRACT,
        "tools": ["lookup_order"],
    },
    "refund-processor": {
        "description": "Issues ONE refund for a verified customer's order, "
                       "within policy. The only agent that can move money.",
        "prompt": "You issue exactly the refund in your brief with "
                  "process_refund. If it is rejected, report the error "
                  "unchanged - do not retry with a different amount."
                  + FACTS_CONTRACT,
        "tools": ["process_refund"],
    },
}

COORDINATOR_PROMPT = f"""You are the customer support coordinator. You talk to
the customer and decide; specialist subagents touch the backend for you.

Process:
1. If the customer explicitly asks for a human, call escalate_to_human
   IMMEDIATELY - do not investigate first.
2. Verify identity (account-verifier) before any refund. Investigate every
   order mentioned (order-investigator, one per order). Independent steps go
   out IN PARALLEL, in the same response.
3. Resolve within policy: refund via refund-processor only after
   verification, and only for the customer's own orders.
4. Escalate (escalate_to_human) when: the fix needs a policy exception, e.g.
   a refund over ${REFUND_AUTO_APPROVE_LIMIT_CENTS // 100}; a tool returns a
   non-retryable business_rule or verification_failed error; or you cannot
   make progress. Do NOT escalate just because the customer is frustrated -
   acknowledge it and resolve the issue.
5. Each subagent sees ONLY the brief you write: include ids, emails and
   amounts it needs.
6. Finish with one short reply to the customer saying what was done, with
   any refund ids or escalation ticket ids. Never claim something happened
   that a tool did not confirm."""

TICKETS = [
    # in policy -> should RESOLVE
    "Hi, I'm Asha Verma (CUS-1001, asha@example.com). Order ORD-5001 arrived "
    "damaged. Can I get a refund?",
    # two independent issues -> parallel lookups, should RESOLVE
    "Asha again, CUS-1001, asha@example.com. ORD-5003 was charged twice - "
    "please refund the duplicate charge. Also, what's the status of ORD-5001?",
    # over the refund limit -> should ESCALATE (policy_exception)
    "This is Diego Ruiz, CUS-1002, diego@example.com. ORD-5002 never arrived "
    "and I want my $1,299 back now. This is ridiculous.",
    # explicit human request -> should ESCALATE immediately
    "I don't want a bot. Get me a real person. CUS-1002.",
]
EXPECT_ESCALATION = [False, False, True, True]


# #############################################################################
# PART A - MESSAGES API: HAND-BUILT DELEGATE TOOL
# #############################################################################

def api_tool(name: str) -> dict:
    """Turn a shared TOOL_SPEC into a Messages API tool definition."""
    # No "strict": True here - strict mode supports only a subset of JSON
    # Schema (pattern/minimum may be rejected). The backend validates anyway.
    return {"name": name, **TOOL_SPECS[name]}


DELEGATE_TOOL = {
    "name": "delegate",
    "description": (
        "Spawn ONE specialist subagent for ONE narrow task and return its "
        "JSON result. The subagent has NO access to this conversation, so "
        "`brief` must be self-contained (ids, email, amounts). Call several "
        "times IN THE SAME RESPONSE to run independent tasks in parallel. "
        "Specialists: "
        + "; ".join(f"{n} - {s['description']}" for n, s in SUBAGENTS.items())
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "enum": list(SUBAGENTS),
                      "description": "Which specialist to spawn."},
            "brief": {"type": "string",
                      "description": "Complete, self-contained task."},
        },
        "required": ["agent", "brief"],
        "additionalProperties": False,
    },
    "strict": True,
}

# Coordinator: delegate + escalate. No backend read/write tools.
COORDINATOR_TOOLS = [DELEGATE_TOOL, api_tool("escalate_to_human")]


def run_backend_tool(block, allowed: list[str]) -> dict:
    # Scope enforced at execution time too: a tool not granted to this agent
    # is refused even if the model somehow names it.
    if block.name not in allowed:
        result = tool_error("permission", False, f"{block.name} not allowed.")
    else:
        result = BACKEND[block.name](**block.input)
    return {"type": "tool_result", "tool_use_id": block.id,
            "content": json.dumps(result),
            "is_error": bool(result.get("isError"))}


async def run_subagent(agent: str, brief: str, max_turns: int = 4) -> str:
    # A brand-new messages list: this is what context isolation IS.
    allowed = SUBAGENTS[agent]["tools"]
    messages = [{"role": "user", "content": brief}]

    for _ in range(max_turns):
        response = await client.messages.create(
            model=SUBAGENT_MODEL,
            max_tokens=1024,
            system=SUBAGENTS[agent]["prompt"],
            tools=[api_tool(t) for t in allowed],   # ONLY this agent's tools
            temperature=0.0,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text")
        messages.append({"role": "user", "content": [
            run_backend_tool(b, allowed)
            for b in response.content if b.type == "tool_use"]})

    raise RuntimeError(f"{agent} exceeded {max_turns} turns")


async def execute_coordinator_call(block) -> dict:
    """One coordinator tool_use block -> one tool_result block."""
    if block.name == "escalate_to_human":
        print(f"   !! escalate_to_human reason={block.input['reason']}")
        return run_backend_tool(block, ["escalate_to_human"])

    agent, brief = block.input["agent"], block.input["brief"]
    print(f"   -> spawn {agent}: {brief[:70]}...")
    try:
        output = await run_subagent(agent, brief)
        return {"type": "tool_result", "tool_use_id": block.id,
                "content": output}
    except Exception as exc:
        # A crashed subagent is reported, not hidden - the coordinator can
        # retry it or escalate with "unable_to_resolve".
        return {"type": "tool_result", "tool_use_id": block.id,
                "is_error": True,
                "content": json.dumps(tool_error(
                    "subagent_failure", True, f"{agent}: {exc}"))}


async def coordinator_messages_api(ticket: str, max_turns: int = 8) -> str:
    messages = [{"role": "user", "content": ticket}]

    for turn in range(1, max_turns + 1):
        response = await client.messages.create(
            model=COORDINATOR_MODEL,
            max_tokens=2048,
            # Identical on every ticket -> cache it (Domain 5).
            system=[{"type": "text", "text": COORDINATOR_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            tools=COORDINATOR_TOOLS,
            tool_choice={"type": "auto", "disable_parallel_tool_use": False},
            temperature=0.0,
            metadata={"user_id": "support-session-0001"},
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            return "".join(b.text for b in response.content if b.type == "text")
        if response.stop_reason != "tool_use":
            raise RuntimeError(f"coordinator stopped: {response.stop_reason}")

        calls = [b for b in response.content if b.type == "tool_use"]
        print(f"   [turn {turn}] {len(calls)} call(s) in parallel")
        # Parallel fan-out: e.g. verify identity + look up two orders at once.
        results = await asyncio.gather(*(execute_coordinator_call(b)
                                         for b in calls))
        messages.append({"role": "user", "content": list(results)})

    # Out of turns = not resolved. Escalate rather than leave the customer
    # hanging - escalation must be reliable even when the agent isn't.
    escalate_to_human("unknown", "unable_to_resolve", ticket,
                      "Coordinator hit its turn limit.", "Review manually.")
    return "Sorry for the wait - a human agent will follow up within 24h."


# #############################################################################
# PART B - AGENT SDK: MCP SERVER + AgentDefinition + HOOK
# #############################################################################
# Map to PART A:
#   BACKEND functions + api_tool()  -> @tool + create_sdk_mcp_server("support")
#   DELEGATE_TOOL + enum            -> agents={name: AgentDefinition(...)}
#   COORDINATOR_TOOLS               -> allowed_tools=["Agent", escalate]
#   run_subagent(allowed=...)       -> AgentDefinition(tools=[mcp__support__x])
#   limit check in process_refund   -> still there, PLUS a PreToolUse hook
#   asyncio.gather / turn loop      -> SDK; max_turns + max_budget_usd
async def coordinator_agent_sdk(ticket: str) -> str:
    from claude_agent_sdk import (
        AgentDefinition, AssistantMessage, ClaudeAgentOptions, HookMatcher,
        ResultMessage, ToolUseBlock, create_sdk_mcp_server, query, tool,
    )

    def mcp_tool(name: str):
        # Wrap a backend function as an MCP tool with the SAME name,
        # description and schema as PART A.
        @tool(name, TOOL_SPECS[name]["description"],
              TOOL_SPECS[name]["input_schema"])
        async def handler(args: dict) -> dict:
            result = BACKEND[name](**args)
            return {"content": [{"type": "text", "text": json.dumps(result)}],
                    "is_error": bool(result.get("isError"))}
        return handler

    support_server = create_sdk_mcp_server(
        name="support", version="1.0.0",
        tools=[mcp_tool(n) for n in TOOL_SPECS])

    def mcp_name(tool_name: str) -> str:
        return f"mcp__support__{tool_name}"

    # Deterministic gate BEFORE the call reaches the backend. Even if a
    # prompt injection convinced the agent, this code still says no.
    async def refund_gate(input_data, tool_use_id, context):
        amount = input_data.get("tool_input", {}).get("amount_cents", 0)
        if amount > REFUND_AUTO_APPROVE_LIMIT_CENTS:
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "Refund exceeds the auto-approve limit. Escalate with "
                    "reason policy_exception."),
            }}
        return {}

    # --- THE SUBAGENTS -------------------------------------------------------
    # One AgentDefinition per role. For each:
    #   description - what the COORDINATOR reads to decide when to use it
    #   prompt      - the subagent's own system prompt (it sees nothing else
    #                 of the conversation - only this + the coordinator's brief)
    #   tools       - its scope: exactly ONE backend tool, and never "Agent",
    #                 so no subagent can spawn another (hub-and-spoke)
    #   model       - per-agent model choice: cheap for narrow lookups
    account_verifier = AgentDefinition(
        description=SUBAGENTS["account-verifier"]["description"],
        prompt=SUBAGENTS["account-verifier"]["prompt"],
        tools=[mcp_name("get_customer")],            # read-only
        model="haiku",
    )
    order_investigator = AgentDefinition(
        description=SUBAGENTS["order-investigator"]["description"],
        prompt=SUBAGENTS["order-investigator"]["prompt"],
        tools=[mcp_name("lookup_order")],            # read-only
        model="haiku",
    )
    refund_processor = AgentDefinition(
        description=SUBAGENTS["refund-processor"]["description"],
        prompt=SUBAGENTS["refund-processor"]["prompt"],
        tools=[mcp_name("process_refund")],          # the ONLY write path
        # Money-moving agent gets the stronger model: misreading the brief
        # here costs real money, unlike a misread lookup.
        model="sonnet",
    )

    options = ClaudeAgentOptions(
        # --- THE COORDINATOR (main agent) ---------------------------------
        system_prompt=COORDINATOR_PROMPT,
        model="sonnet",
        mcp_servers={"support": support_server},

        # Registering the subagents is what makes the built-in Agent tool
        # able to spawn them. The keys are the names the coordinator uses
        # (Agent tool input: subagent_type="refund-processor").
        agents={
            "account-verifier": account_verifier,
            "order-investigator": order_investigator,
            "refund-processor": refund_processor,
        },

        # Coordinator scope: delegate (Agent) + escalate only. It cannot call
        # get_customer / lookup_order / process_refund itself, so it can't
        # skip verification or bypass the refund-processor.
        allowed_tools=["Agent", mcp_name("escalate_to_human")],
        hooks={"PreToolUse": [HookMatcher(matcher=mcp_name("process_refund"),
                                          hooks=[refund_gate])]},
        max_turns=15,           # runaway guardrail
        max_budget_usd=0.50,    # per-ticket dollar guardrail
    )

    spawned: dict[str, str] = {}   # Agent tool_use id -> subagent name

    async for message in query(prompt=ticket, options=options):
        if isinstance(message, AssistantMessage):
            # parent_tool_use_id is None for the coordinator's own messages,
            # and the spawning Agent call's id for a subagent's messages -
            # that's how you tell WHICH agent is acting in the stream.
            parent = getattr(message, "parent_tool_use_id", None)
            who = spawned.get(parent, "coordinator") if parent else "coordinator"
            calls = [b for b in message.content if isinstance(b, ToolUseBlock)]

            delegations = [b for b in calls if b.name in ("Agent", "Task")]
            if len(delegations) > 1:
                print(f"   [{who}] parallel fan-out to {len(delegations)} subagents")
            for b in calls:
                if b in delegations:
                    name = b.input.get("subagent_type", "?")
                    spawned[b.id] = name
                    print(f"   [{who}] -> spawn {name}: "
                          f"{b.input.get('prompt', '')[:60]}...")
                elif b.name.endswith("escalate_to_human"):
                    print(f"   [{who}] !! escalate_to_human "
                          f"reason={b.input.get('reason')}")
                else:
                    print(f"   [{who}] calls {b.name}({b.input})")
        elif isinstance(message, ResultMessage):
            print(f"   [sdk] subtype={message.subtype} turns={message.num_turns} "
                  f"cost=${message.total_cost_usd:.4f}")
            if message.subtype != "success":
                # A guardrail stopped it: escalate, never report as resolved.
                escalate_to_human("unknown", "unable_to_resolve", ticket,
                                  f"Agent stopped: {message.subtype}.",
                                  "Review manually.")
                return "Sorry for the wait - a human agent will follow up."
            return message.result
    return ""


# =============================================================================
# RUN THE TICKETS AND MEASURE FCR *AND* ESCALATION CORRECTNESS
# =============================================================================
async def run_tickets(coordinator) -> None:
    resolved = correct_escalation = 0
    for i, (ticket, expected) in enumerate(zip(TICKETS, EXPECT_ESCALATION), 1):
        reset_backend()
        print(f"\n--- ticket {i}: {ticket[:60]}...")
        reply = await coordinator(ticket)
        escalated = bool(ESCALATIONS)
        resolved += not escalated
        correct_escalation += escalated == expected
        print(f"   reply: {reply}")
        if escalated:
            print(f"   handoff: {json.dumps(ESCALATIONS[-1])}")
        print(f"   -> {'ESCALATED' if escalated else 'RESOLVED'} "
              f"(expected {'ESCALATE' if expected else 'RESOLVE'})")

    n = len(TICKETS)
    # FCR alone rewards never escalating. Report both: a high FCR with wrong
    # escalation decisions is a failing agent.
    print(f"\nFCR: {resolved}/{n} = {resolved / n:.0%}   "
          f"escalation decisions correct: {correct_escalation}/{n}")
    print("(This tiny set is half escalation cases by design; measure the 80% "
          "FCR target on real ticket mix.)")


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("set ANTHROPIC_API_KEY first")

    which = sys.argv[1].lower() if len(sys.argv) > 1 else "ab"

    if "a" in which:
        print("=== PART A: Messages API + hand-built delegate tool ===")
        asyncio.run(run_tickets(coordinator_messages_api))

    if "b" in which:
        print("\n=== PART B: Agent SDK - MCP server + AgentDefinition + hook ===")
        asyncio.run(run_tickets(coordinator_agent_sdk))
