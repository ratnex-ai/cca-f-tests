"""
=============================================================================
 CCA-F CONCEPT LAB  -  runnable, no API key needed
=============================================================================
 Run it:   python3 ccaf_concepts.py
 Every section prints what is happening so you can SEE the mechanics.

 A "FakeClaude" stands in for the real API so the loop is visible.
 Real API calls are shown in comments marked  # >>> REAL API <<<
=============================================================================
"""

import json
from dataclasses import dataclass, field
from typing import Any


# =============================================================================
# PYTHON NOTE 0 - the shapes you will see everywhere
# =============================================================================
# dict   {"key": "value"}      -> JSON object. This IS the API wire format.
# list   ["a", "b"]            -> JSON array
# None                         -> JSON null
# True/False                   -> JSON true/false  (capital letters in Python!)
# f"..."                       -> f-string: f"hi {name}" inserts the variable
# def name(arg: str) -> dict:  -> function; ": str" and "-> dict" are TYPE HINTS.
#                                 They are documentation only. Python ignores them.
# =============================================================================


# =============================================================================
# SECTION 1 - TOOL DEFINITIONS (Domain 2: Tool Design)
# =============================================================================
# A tool definition is just a dict with 3 keys: name, description, input_schema.
# input_schema is JSON Schema. The DESCRIPTION is what the model reads to decide
# whether to call it. Exam framing: the description is the tool's user interface.

# --- BAD: the exam's classic "minimal description" anti-pattern ---
BAD_TOOL = {
    "name": "analyze_content",
    "description": "Analyzes content.",          # <- model cannot disambiguate
    "input_schema": {"type": "object", "properties": {"data": {"type": "string"}}},
}

# --- GOOD: Purpose / Input / Trigger / Output, plus boundaries ---
GET_ORDER = {
    "name": "get_order",
    "description": (
        "PURPOSE: Retrieve a single order record from the fulfilment database.\n"
        "INPUT: order_id, format 'ORD-' followed by 8 digits (e.g. ORD-10029384).\n"
        "WHEN TO USE: after the customer has been verified, when you need order "
        "status, line items, or the amount paid.\n"
        "WHEN NOT TO USE: to look up a customer (use get_customer) or to search "
        "by email/date (use search_orders).\n"
        "OUTPUT: JSON with order_id, status, total_cents, placed_at, items[].\n"
        "EDGE CASES: unknown order_id returns a validation error, not an empty "
        "object. An order with zero items is a valid result, not a failure."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "e.g. ORD-10029384"}
        },
        "required": ["order_id"],   # required is a LIST of property names
    },
}

PROCESS_REFUND = {
    "name": "process_refund",
    "description": (
        "PURPOSE: Issue a refund against a verified order.\n"
        "INPUT: order_id and amount_cents (integer, minor units - 2599 = $25.99).\n"
        "WHEN TO USE: only after get_customer has returned a verified customer_id "
        "AND get_order confirms the order is refund-eligible.\n"
        "OUTPUT: refund_id and new order status.\n"
        "EDGE CASES: amounts above the agent authorisation limit are rejected "
        "with a business error - escalate instead of retrying."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string"},
            "amount_cents": {"type": "integer"},
        },
        "required": ["order_id", "amount_cents"],
    },
}

TOOLS = [GET_ORDER, PROCESS_REFUND]


# =============================================================================
# SECTION 2 - STRUCTURED ERROR RETURNS (Domain 2.2 + Domain 5.3)
# =============================================================================
# THE single most tested "tool design" idea. A tool must never return
# "Operation failed". The agent needs enough metadata to CHOOSE a recovery path.
#
# Four categories the exam uses:
#   transient   - timeout, 503, rate limit        -> retryable, back off
#   validation  - malformed input                 -> NOT retryable as-is, fix input
#   business    - policy violation (refund > cap) -> NOT retryable, escalate
#   permission  - missing scope / auth            -> NOT retryable, escalate
#
# Also critical: an ACCESS FAILURE is not the same as a VALID EMPTY RESULT.
#   "search returned 0 rows"  = success, empty. Do NOT retry.
#   "search timed out"        = failure.       DO consider retry.

@dataclass
class ToolOutcome:
    """PYTHON NOTE: @dataclass auto-writes __init__ for you.
    ToolOutcome(ok=True, data={...}) just works - no boilerplate."""
    ok: bool
    data: Any = None
    error_category: str | None = None   # "|" means "or" -> str OR None
    is_retryable: bool = False
    message: str = ""
    attempted: str = ""                 # what the tool tried - helps coordinator
    partial: Any = None                 # partial results survive the failure


def to_mcp_block(tool_use_id: str, outcome: ToolOutcome) -> dict:
    """Convert our outcome into the wire-format tool_result block.

    MCP / Messages API convention: failure is signalled with is_error=True
    (MCP spec calls the field isError). The CONTENT still carries structure."""
    if outcome.ok:
        body = json.dumps(outcome.data)          # json.dumps: dict -> JSON string
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": body}

    body = json.dumps({
        "errorCategory": outcome.error_category,   # transient/validation/business/permission
        "isRetryable": outcome.is_retryable,
        "message": outcome.message,                # human-readable, agent can relay it
        "attempted": outcome.attempted,
        "partialResults": outcome.partial,
    })
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": body,
        "is_error": True,                          # <- the isError flag
    }


# ---- the actual local tool implementations -----------------------------------
FAKE_DB = {
    "ORD-10029384": {"order_id": "ORD-10029384", "status": "delivered",
                     "total_cents": 84900, "placed_at": "2026-07-02",
                     "items": [{"sku": "KB-88", "qty": 1}]},
}
AGENT_REFUND_LIMIT_CENTS = 50000   # $500 - the exam's favourite threshold


def tool_get_order(order_id: str) -> ToolOutcome:
    if not order_id.startswith("ORD-"):
        return ToolOutcome(
            ok=False, error_category="validation", is_retryable=False,
            message="order_id must look like ORD-12345678.",
            attempted=f"get_order({order_id})",
        )
    row = FAKE_DB.get(order_id)      # .get() returns None instead of crashing
    if row is None:
        # NOTE: "not found" is a VALID EMPTY RESULT, not an access failure.
        return ToolOutcome(ok=True, data={"found": False, "order_id": order_id})
    return ToolOutcome(ok=True, data=row)


def tool_process_refund(order_id: str, amount_cents: int) -> ToolOutcome:
    if amount_cents > AGENT_REFUND_LIMIT_CENTS:
        return ToolOutcome(
            ok=False, error_category="business", is_retryable=False,
            message=(f"Refund of ${amount_cents/100:.2f} exceeds the agent "
                     f"authorisation limit of ${AGENT_REFUND_LIMIT_CENTS/100:.2f}. "
                     f"Escalate to a human agent - do not retry."),
            attempted=f"process_refund({order_id}, {amount_cents})",
        )
    return ToolOutcome(ok=True, data={"refund_id": "RFD-77", "status": "refunded"})


# Registry: tool name -> python function. The loop uses this to dispatch.
LOCAL_TOOLS = {
    "get_order": tool_get_order,
    "process_refund": tool_process_refund,
}


# =============================================================================
# SECTION 3 - THE AGENTIC LOOP (Domain 1.1)  *** highest-yield section ***
# =============================================================================
# THE RULE:  loop while stop_reason == "tool_use";  stop on "end_turn".
#
# ANTI-PATTERNS the exam will offer you as wrong answers:
#   X  parsing assistant text for "I'm done" / "task complete"
#   X  using an iteration cap as the PRIMARY terminator (it's a guardrail only)
#   X  checking whether the response contains text blocks
#   X  rebuilding `messages` from scratch each turn (history must accumulate)
#
# Also remember: the API is STATELESS. You resend the whole history every call.
# And: CLAUDE NEVER EXECUTES YOUR TOOL. Your code does. Claude only *requests*.

class FakeClaude:
    """Scripted stand-in so you can watch the loop without an API key."""
    def __init__(self, script):
        self.script = script          # list of canned responses
        self.i = 0                    # which one we're on

    def create(self, messages, tools, tool_choice=None):
        resp = self.script[self.i]
        self.i += 1
        return resp


def agentic_loop(client, messages: list, tools: list, max_turns: int = 12):
    """Canonical loop. Read this until it is muscle memory."""
    turns = 0
    while True:
        turns += 1
        if turns > max_turns:
            # GUARDRAIL, not the terminator. Runaway protection only.
            raise RuntimeError("max_turns guardrail hit")

        # >>> REAL API <<<
        # resp = client.messages.create(
        #     model="claude-sonnet-4-6", max_tokens=1024,
        #     messages=messages, tools=tools)
        resp = client.create(messages=messages, tools=tools)

        print(f"  [turn {turns}] stop_reason = {resp['stop_reason']}")

        # 1. Append the assistant's turn to history. ALWAYS. Even mid-tool-call.
        messages.append({"role": "assistant", "content": resp["content"]})

        # 2. THE ONLY BRANCH THAT MATTERS
        if resp["stop_reason"] != "tool_use":
            # end_turn / max_tokens / stop_sequence / refusal / pause_turn
            return resp, messages

        # 3. Execute every tool_use block the model emitted.
        #    Multiple blocks in ONE response = PARALLEL tool use.
        results = []
        for block in resp["content"]:
            if block["type"] != "tool_use":
                continue                     # skip text/thinking blocks
            fn = LOCAL_TOOLS[block["name"]]
            # **block["input"] unpacks {"order_id": "X"} into order_id="X"
            outcome = fn(**block["input"])
            print(f"           -> ran {block['name']}, ok={outcome.ok} "
                  f"cat={outcome.error_category}")
            results.append(to_mcp_block(block["id"], outcome))

        # 4. Feed results back as a USER message. Results must immediately
        #    follow their tool_use blocks in history.
        messages.append({"role": "user", "content": results})
        # loop repeats -> the model now reasons WITH the new information


# =============================================================================
# SECTION 4 - LOCAL TOOLS vs MCP TOOLS vs SERVER TOOLS
# =============================================================================
# LOCAL (client) TOOL   you define the schema, YOU execute it, YOU send back
#                       a tool_result block. Blocks: tool_use / tool_result.
#
# MCP TOOL              lives on an MCP server. Discovered at connection time.
#                       The server executes it; the loop does not stop for you
#                       to run anything. Blocks: mcp_tool_use / mcp_tool_result.
#                       Failure carries the isError flag.
#
# SERVER TOOL           Anthropic-hosted (web_search etc). Executed server-side;
#                       may return stop_reason "pause_turn" if the internal
#                       sampling loop hits its limit -> you continue the turn.
#
# CONFIG SCOPING (Domain 2.4) - very testable:
#   .mcp.json          project scope  -> committed, shared with the team
#   ~/.claude.json     user scope     -> personal / experimental, NOT shared
#   Secrets go in via env expansion:  "GITHUB_TOKEN": "${GITHUB_TOKEN}"

MCP_JSON_EXAMPLE = {
    "mcpServers": {
        "github": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"},   # never commit the value
        }
    }
}


# =============================================================================
# SECTION 5 - tool_choice (Domain 2.3 + 4.3)
# =============================================================================
#  {"type": "auto"}                      model may answer with plain text
#  {"type": "any"}                       model MUST call some tool (any one)
#  {"type": "tool", "name": "extract"}   model MUST call THAT tool
#  {"type": "none"}                      no tools this turn
#
# Exam mapping:
#   "guarantee structured output, unknown doc type, several schemas"  -> any
#   "force extract_metadata to run first, then enrich"                -> tool
#   "normal agent that may also just talk to the user"                -> auto
TOOL_CHOICE_FORCED = {"type": "tool", "name": "get_order"}


# =============================================================================
# SECTION 6 - COORDINATOR + SUBAGENTS (Domain 1.2 / 1.3)
# =============================================================================
# HUB-AND-SPOKE. Subagents never talk to each other. Everything routes through
# the coordinator -> observability, one error-handling policy, controlled flow.
#
# THE #1 TESTED FACT: subagents DO NOT inherit the coordinator's context.
# Whatever they need must be written into their prompt explicitly.
#
# In Claude Code / Agent SDK the spawn mechanism is the Task tool, and the
# coordinator's allowedTools MUST include "Task" or it cannot spawn anything.
# Parallel subagents = emit MULTIPLE Task calls in ONE assistant response.

@dataclass
class AgentDefinition:
    """Mirrors the Agent SDK shape: description + prompt + tool restrictions."""
    name: str
    description: str
    system_prompt: str
    allowed_tools: list = field(default_factory=list)  # see PYTHON NOTE below
    # PYTHON NOTE: never write `allowed_tools: list = []` - that one list would
    # be shared by every instance. default_factory=list makes a fresh one.


SEARCH_AGENT = AgentDefinition(
    name="search",
    description="Finds primary sources on the web for a narrow subtopic.",
    system_prompt=(
        "Find sources for the subtopic you are given. Return STRUCTURED claims: "
        "each claim must carry source_url, publication_date, and an excerpt. "
        "Never return prose reasoning chains."
    ),
    allowed_tools=["web_search", "load_document"],   # scoped - no refund tools!
)

SYNTHESIS_AGENT = AgentDefinition(
    name="synthesis",
    description="Merges findings from search + analysis into a final report.",
    system_prompt=(
        "You will receive complete findings inline. You have NO memory of the "
        "prior agents. Preserve every claim->source mapping. Where sources "
        "conflict, annotate the conflict with both values and attributions "
        "rather than silently picking one. Flag topic areas with no coverage."
    ),
    allowed_tools=["verify_fact"],   # ONE scoped cross-role tool, not web_search
)


def coordinator(query: str, subagent_findings: dict) -> dict:
    """Coordinator responsibilities, all four of them."""
    # 1. DECOMPOSE - and route dynamically, not always the full pipeline
    needs_web = "latest" in query or "2026" in query
    plan = ["search"] if needs_web else []
    plan.append("analysis")

    # 2. DELEGATE with EXPLICIT context (isolated context!)
    synthesis_prompt = (
        "Produce a report on: " + query + "\n\n"
        "FINDINGS (you have no other context):\n"
        + json.dumps(subagent_findings, indent=2)
    )

    # 3. AGGREGATE + 4. EVALUATE FOR GAPS -> re-delegate if thin
    gaps = [k for k, v in subagent_findings.items() if not v]
    # PYTHON NOTE: that's a LIST COMPREHENSION - "collect k for each key/value
    # pair where v is empty". Equivalent to a for-loop that appends.
    return {"plan": plan, "synthesis_prompt": synthesis_prompt, "gaps": gaps}


# =============================================================================
# SECTION 7 - STRUCTURED OUTPUT VIA TOOL USE (Domain 4.3 / 4.4)
# =============================================================================
# Reliable JSON = define an "extraction tool" whose input_schema IS your target
# schema, then read the tool_use block's `input`. This removes SYNTAX errors.
# It does NOT remove SEMANTIC errors (line items not summing to the total).
#
# Schema design rules the exam loves:
#   - nullable/optional for fields the doc may genuinely lack -> stops fabrication
#   - enum + "other" + a free-text detail field -> extensible categories
#   - enum value "unclear" for ambiguity
#   - emit calculated_total AND stated_total -> lets you detect the mismatch

EXTRACT_INVOICE = {
    "name": "extract_invoice",
    "description": "Emit the invoice fields exactly as found in the document.",
    "input_schema": {
        "type": "object",
        "properties": {
            "vendor": {"type": ["string", "null"]},         # nullable, not required
            "stated_total_cents": {"type": ["integer", "null"]},
            "calculated_total_cents": {"type": ["integer", "null"]},
            "conflict_detected": {"type": "boolean"},
            "doc_type": {"type": "string",
                         "enum": ["invoice", "receipt", "other", "unclear"]},
            "doc_type_detail": {"type": ["string", "null"]},
            "line_items": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "desc": {"type": "string"},
                    "amount_cents": {"type": "integer"}}},
            },
        },
        "required": ["doc_type", "line_items", "conflict_detected"],
    },
}
# >>> REAL API <<<  force it so you always get JSON, never chatty text:
# client.messages.create(..., tools=[EXTRACT_INVOICE],
#                        tool_choice={"type": "tool", "name": "extract_invoice"})


def validate_and_retry(extraction: dict, attempt: int) -> tuple[bool, str]:
    """Retry-with-error-FEEDBACK. Retrying an identical request is useless."""
    errors = []
    total = sum(i["amount_cents"] for i in extraction["line_items"])
    if extraction.get("stated_total_cents") not in (None, total):
        errors.append(
            f"line items sum to {total} but stated_total_cents is "
            f"{extraction['stated_total_cents']}; re-check for missed items"
        )
    if not errors:
        return True, ""
    # KEY: the retry prompt must contain the doc + the failed output + the
    # SPECIFIC errors. And note: if the info is simply ABSENT from the source,
    # no amount of retrying will help. That's the exam's trap.
    return False, "; ".join(errors)


# =============================================================================
# SECTION 8 - HOOKS (Domain 1.5) - deterministic vs probabilistic
# =============================================================================
# Prompt instruction = probabilistic. Non-zero failure rate.
# Hook            = deterministic. Code, not persuasion.
# If the question says "must ALWAYS", "guaranteed", "compliance", "never allow"
#   -> the answer is a HOOK / programmatic gate, never "add it to the prompt".
#
# PreToolUse-style interception: block the call before it happens.
# PostToolUse:                   normalise the RESULT before the model sees it.

def pre_tool_use_hook(tool_name: str, tool_input: dict, state: dict) -> dict | None:
    """Return None to allow; return a dict to BLOCK and substitute a result."""
    if tool_name == "process_refund":
        if not state.get("verified_customer_id"):           # prerequisite gate
            return {"blocked": True,
                    "reason": "identity not verified; call get_customer first"}
        if tool_input["amount_cents"] > AGENT_REFUND_LIMIT_CENTS:
            return {"blocked": True, "reason": "over $500 - route to human"}
    return None


def post_tool_use_hook(tool_name: str, raw: dict) -> dict:
    """Normalise heterogeneous formats BEFORE the model reasons about them."""
    out = dict(raw)
    if isinstance(raw.get("placed_at"), int):        # Unix seconds from one MCP server
        out["placed_at"] = "1970-01-01+" + str(raw["placed_at"])  # -> ISO 8601
    status_map = {0: "pending", 1: "shipped", 2: "delivered"}     # numeric codes
    if isinstance(raw.get("status"), int):
        out["status"] = status_map.get(raw["status"], "unknown")
    # Also TRIM: keep only relevant fields so context doesn't bloat (Domain 5.1)
    keep = ("order_id", "status", "total_cents", "placed_at")
    return {k: v for k, v in out.items() if k in keep}


# =============================================================================
# SECTION 9 - CONTEXT MANAGEMENT (Domain 5.1)
# =============================================================================
# Progressive summarisation destroys NUMBERS, DATES, IDs and customer-stated
# expectations. Fix = a persistent "case facts" block held OUTSIDE the
# summarised history and re-injected verbatim into every prompt.
#
# "Lost in the middle": put key findings FIRST, use explicit section headers.

CASE_FACTS = {          # never summarised, always re-sent verbatim
    "customer_id": "CUS-4410",
    "order_id": "ORD-10029384",
    "amount_disputed_cents": 84900,
    "promised_by_agent": "refund within 5 business days",
    "verified_at": "2026-08-12T09:02:00Z",
}


def build_prompt(summary: str, recent_turns: list) -> str:
    return (
        "## CASE FACTS (authoritative, never summarised)\n"
        + json.dumps(CASE_FACTS, indent=2)
        + "\n\n## CONVERSATION SUMMARY\n" + summary
        + "\n\n## RECENT TURNS\n" + "\n".join(recent_turns)
    )


# =============================================================================
# DEMO
# =============================================================================
def demo():
    print("\n=== 1. AGENTIC LOOP: two tool turns then end_turn ===")
    script = [
        {"stop_reason": "tool_use", "content": [
            {"type": "text", "text": "Let me look that order up."},
            {"type": "tool_use", "id": "tu_1", "name": "get_order",
             "input": {"order_id": "ORD-10029384"}}]},
        {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "tu_2", "name": "process_refund",
             "input": {"order_id": "ORD-10029384", "amount_cents": 84900}}]},
        {"stop_reason": "end_turn", "content": [
            {"type": "text", "text": "That refund is above my limit - escalating."}]},
    ]
    msgs = [{"role": "user", "content": "Refund my ORD-10029384, it arrived broken."}]
    final, msgs = agentic_loop(FakeClaude(script), msgs, TOOLS)
    print("  final text:", final["content"][0]["text"])
    print(f"  history length: {len(msgs)} messages (it ACCUMULATED)")

    print("\n=== 2. STRUCTURED ERROR the agent can act on ===")
    bad = tool_process_refund("ORD-10029384", 84900)
    print(json.dumps(json.loads(to_mcp_block("tu_x", bad)["content"]), indent=2))

    print("\n=== 3. VALID EMPTY RESULT is NOT an error ===")
    print(tool_get_order("ORD-00000000"))

    print("\n=== 4. HOOK blocks what a prompt only discourages ===")
    print(pre_tool_use_hook("process_refund", {"amount_cents": 84900}, {}))
    print(pre_tool_use_hook("process_refund", {"amount_cents": 1000},
                            {"verified_customer_id": "CUS-4410"}))

    print("\n=== 5. PostToolUse normalisation + trimming ===")
    print(post_tool_use_hook("get_order",
          {"order_id": "ORD-1", "status": 2, "placed_at": 1750000000,
           "total_cents": 999, "internal_shard": "eu-3", "debug_blob": "x" * 40}))

    print("\n=== 6. COORDINATOR routes dynamically + spots gaps ===")
    print(json.dumps(coordinator("latest 2026 battery research",
                                 {"search": [{"claim": "x", "source_url": "u"}],
                                  "analysis": []})["gaps"], indent=2))

    print("\n=== 7. SEMANTIC validation survives a perfect schema ===")
    ok, err = validate_and_retry(
        {"line_items": [{"desc": "kb", "amount_cents": 100},
                        {"desc": "mouse", "amount_cents": 200}],
         "stated_total_cents": 500}, attempt=1)
    print("valid:", ok, "| feedback for retry:", err)


if __name__ == "__main__":
    # PYTHON NOTE: this guard means "only run demo() when this file is executed
    # directly, not when another file imports it."
    demo()