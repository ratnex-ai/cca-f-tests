"""
01 - THE AGENTIC LOOP  (CCA-F Domain 1.1)
=============================================================================
Real anthropic SDK. Real SQLite database. No stubs.

    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 01_agentic_loop.py

The tools below actually query a real database file that this script creates.
=============================================================================
"""

import json
import os
import sqlite3

import anthropic

DB_PATH = "support.db"
AGENT_REFUND_LIMIT_CENTS = 50_000  # $500
# PYTHON NOTE: 50_000 == 50000. Underscores are just visual separators.


# ---------------------------------------------------------------------------
# Build a real database so the tools have something real to do.
# ---------------------------------------------------------------------------
def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        DROP TABLE IF EXISTS orders;
        CREATE TABLE orders (
            order_id     TEXT PRIMARY KEY,
            customer_id  TEXT NOT NULL,
            status       TEXT NOT NULL,
            total_cents  INTEGER NOT NULL,
            placed_at    TEXT NOT NULL,
            refunded     INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO orders VALUES
            ('ORD-10029384','CUS-4410','delivered', 4250,'2026-07-02',0),
            ('ORD-10029385','CUS-4410','delivered',84900,'2026-07-11',0);
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# TOOL DEFINITIONS - the exact dicts sent in the `tools` request parameter.
# The docs say: at least 3-4 sentences per description; state what the tool
# does, when to use it, when NOT to, and what it returns.
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "get_order",
        "description": (
            "Retrieve a single order record from the fulfilment database. Use "
            "this after the customer has been identified, whenever you need an "
            "order's status, total, or refund eligibility. Do not use it to "
            "find a customer or to search by email or date range. Returns "
            "order_id, customer_id, status, total_cents (an integer in minor "
            "units, so 4250 means $42.50), placed_at, and refunded. An "
            "order_id that does not exist returns found=false, which is a "
            "valid answer and not an error."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "Order identifier, format ORD-12345678",
                }
            },
            "required": ["order_id"],
        },
        # input_examples is a real, current field - schema-validated examples
        # that show Claude concrete well-formed calls.
        "input_examples": [{"order_id": "ORD-10029384"}],
    },
    {
        "name": "process_refund",
        "description": (
            "Issue a refund against an order that has already been retrieved "
            "with get_order. Use it only once you have confirmed the order "
            "exists, is delivered, and has not already been refunded. Do not "
            "use it to check refund eligibility. amount_cents is an integer in "
            "minor units. Refunds above the agent authorisation limit are "
            "rejected with a business error and must be escalated to a human "
            "rather than retried."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "amount_cents": {"type": "integer", "minimum": 1},
            },
            "required": ["order_id", "amount_cents"],
        },
        "input_examples": [{"order_id": "ORD-10029384", "amount_cents": 4250}],
    },
]


# ---------------------------------------------------------------------------
# TOOL IMPLEMENTATIONS - real SQL. Each returns (payload, is_error).
# ---------------------------------------------------------------------------
def get_order(order_id: str) -> tuple[dict, bool]:
    if not order_id.startswith("ORD-"):
        return {
            "errorCategory": "validation",
            "isRetryable": False,
            "message": "order_id must be formatted ORD-12345678.",
            "attempted": f"get_order({order_id!r})",
        }, True
        # PYTHON NOTE: {order_id!r} inside an f-string calls repr() - it adds
        # the quotes, so you see ORD-1 as 'ORD-1' and can spot whitespace bugs.

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row      # rows behave like dicts instead of tuples
    row = conn.execute(
        "SELECT * FROM orders WHERE order_id = ?", (order_id,)
    ).fetchone()
    conn.close()

    if row is None:
        # A miss is a VALID EMPTY RESULT. Not an error. Do not set is_error.
        return {"found": False, "order_id": order_id}, False
    return {"found": True, **dict(row)}, False
    # PYTHON NOTE: **dict(row) splices the row's keys into the new dict.


def process_refund(order_id: str, amount_cents: int) -> tuple[dict, bool]:
    if amount_cents > AGENT_REFUND_LIMIT_CENTS:
        return {
            "errorCategory": "business",
            "isRetryable": False,
            "message": (
                f"Refund of ${amount_cents / 100:.2f} exceeds the agent "
                f"authorisation limit of ${AGENT_REFUND_LIMIT_CENTS / 100:.2f}. "
                f"Escalate to a human agent. Do not retry."
            ),
            "attempted": f"process_refund({order_id!r}, {amount_cents})",
        }, True

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "UPDATE orders SET refunded = 1, status = 'refunded' "
        "WHERE order_id = ? AND refunded = 0",
        (order_id,),
    )
    conn.commit()
    changed = cur.rowcount
    conn.close()

    if changed == 0:
        return {
            "errorCategory": "business",
            "isRetryable": False,
            "message": "Order not found or already refunded.",
            "attempted": f"process_refund({order_id!r}, {amount_cents})",
        }, True
    return {"refunded": True, "order_id": order_id,
            "amount_cents": amount_cents}, False


# Name -> function. The loop dispatches through this.
IMPLEMENTATIONS = {"get_order": get_order, "process_refund": process_refund}


# ---------------------------------------------------------------------------
# THE LOOP
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a customer support resolution agent.\n"
    "Always retrieve the order with get_order before attempting any refund.\n"
    "If a tool returns errorCategory 'business', do not retry it. Explain the "
    "outcome to the customer and state that you are escalating.\n"
    "Refund amounts are in minor units (cents)."
)


def run_agent(user_message: str, max_turns: int = 12) -> str:
    client = anthropic.Anthropic()      # reads ANTHROPIC_API_KEY from the env

    messages: list[dict] = [{"role": "user", "content": user_message}]

    for turn in range(1, max_turns + 1):
        response = client.messages.create(
            model="claude-opus-5",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            # tool_choice defaults to {"type": "auto"} whenever tools are given
        )

        print(f"[turn {turn}] stop_reason={response.stop_reason}")

        # 1) Append the assistant turn to history, ALWAYS.
        #    response.content is a list of SDK block objects; the SDK accepts
        #    them back verbatim on the next request.
        messages.append({"role": "assistant", "content": response.content})

        # 2) The only branch that matters.
        if response.stop_reason != "tool_use":
            # end_turn | max_tokens | stop_sequence | refusal | pause_turn
            if response.stop_reason == "max_tokens":
                raise RuntimeError("output truncated - raise max_tokens")
            if response.stop_reason == "refusal":
                raise RuntimeError("model declined the request")
            return "".join(
                b.text for b in response.content if b.type == "text"
            )

        # 3) Execute every tool_use block. Several blocks in one response is
        #    parallel tool use - run them all before replying.
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue                        # skip text / thinking blocks
            fn = IMPLEMENTATIONS[block.name]
            payload, is_error = fn(**block.input)
            print(f"          ran {block.name}({block.input}) "
                  f"-> is_error={is_error}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,        # must match the tool_use id
                "content": json.dumps(payload),
                "is_error": is_error,
            })

        # 4) Tool results go back as a USER message, immediately after the
        #    assistant message that requested them.
        messages.append({"role": "user", "content": tool_results})

    # max_turns is a runaway GUARDRAIL, never the primary terminator.
    raise RuntimeError(f"guardrail: exceeded {max_turns} turns")


# ---------------------------------------------------------------------------
# The same loop, handed to the SDK's tool runner instead of written by hand.
# Same semantics, the SDK does the stop_reason branching for you.
# ---------------------------------------------------------------------------
def run_agent_with_tool_runner(user_message: str) -> str:
    client = anthropic.Anthropic()
    runner = client.beta.messages.tool_runner(
        model="claude-opus-5",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=[{"role": "user", "content": user_message}],
    )
    final = runner.until_done()
    return "".join(b.text for b in final.content if b.type == "text")


if __name__ == "__main__":
    init_db()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("set ANTHROPIC_API_KEY first")

    print("--- in-limit refund ---")
    print(run_agent("Order ORD-10029384 arrived damaged. Please refund it."))

    print("\n--- over-limit refund: expect a business error and escalation ---")
    print(run_agent("Refund ORD-10029385 in full, it was never delivered."))