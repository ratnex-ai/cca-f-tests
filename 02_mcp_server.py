"""
02 - A REAL MCP SERVER + THE REAL MCP CONNECTOR  (CCA-F Domain 2.2 / 2.4)
=============================================================================
Two halves:
  PART A - a genuine MCP server (python `mcp` SDK, v2.x) exposing tools and a
           resource, returning STRUCTURED errors with the isError flag.
  PART B - the genuine Messages API call that connects to a remote MCP server.

    pip install "mcp[cli]" anthropic
    python3 02_mcp_server.py          # runs the server over stdio

Wire it into Claude Code by adding it to .mcp.json (see the bottom of the file).
=============================================================================
"""

import json
import sqlite3

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

DB_PATH = "support.db"           # created by 01_agentic_loop.py
AGENT_REFUND_LIMIT_CENTS = 50_000

server = MCPServer("support-tools")
# PYTHON NOTE: `server` is an object. The @server.tool() lines below are
# DECORATORS - they register the function with that object. The function still
# works as a normal Python function afterwards.


# ---------------------------------------------------------------------------
# The structured-error helper. This is the whole of Domain 2.2 in one function.
# ---------------------------------------------------------------------------
def tool_error(
    category: str,          # "transient" | "validation" | "business" | "permission"
    message: str,
    *,                      # everything after * must be passed by NAME
    retryable: bool = False,
    attempted: str = "",
    partial: object = None,
) -> CallToolResult:
    """Build an MCP error result the agent can actually reason about.

    Why not just raise? A raised exception becomes an opaque string. The agent
    then cannot tell "retry in 2s" from "never retry, escalate". Categories +
    isRetryable are what let it choose a recovery path.
    """
    body = {
        "errorCategory": category,
        "isRetryable": retryable,
        "message": message,
        "attempted": attempted,
        "partialResults": partial,
    }
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(body))],
        is_error=True,                      # serialises to isError on the wire
    )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# TOOLS. The docstring becomes the tool description the model reads, so it is
# written for a model, not for a maintainer.
# ---------------------------------------------------------------------------
@server.tool()
def get_order(order_id: str) -> CallToolResult:
    """Retrieve one order from the fulfilment database.

    Use after the customer is identified, when you need an order's status,
    total, or refund eligibility. Do not use it to look up a customer, and do
    not use it to search by email or date.

    order_id must be formatted ORD- followed by 8 digits.
    Returns order_id, customer_id, status, total_cents (minor units: 4250 is
    $42.50), placed_at, refunded.

    An order_id with no match returns found=false. That is a successful query
    with no rows, not a failure - do not retry it.
    """
    if not order_id.startswith("ORD-"):
        return tool_error(
            "validation",
            "order_id must be formatted ORD-12345678.",
            attempted=f"get_order({order_id!r})",
        )
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()
    except sqlite3.OperationalError as exc:
        # Lock contention / DB busy is genuinely TRANSIENT -> retryable.
        return tool_error(
            "transient", f"Database unavailable: {exc}",
            retryable=True, attempted=f"get_order({order_id!r})",
        )

    payload = {"found": False, "order_id": order_id} if row is None \
        else {"found": True, **dict(row)}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        is_error=False,
    )


@server.tool()
def process_refund(order_id: str, amount_cents: int) -> CallToolResult:
    """Issue a refund against an order previously retrieved with get_order.

    Use only after get_order confirms the order exists, is delivered, and is
    not already refunded. amount_cents is an integer in minor units.

    Refunds above the agent authorisation limit return errorCategory
    "business" with isRetryable false. Escalate those to a human; retrying an
    identical request will produce an identical rejection.
    """
    if amount_cents <= 0:
        return tool_error(
            "validation", "amount_cents must be a positive integer.",
            attempted=f"process_refund({order_id!r}, {amount_cents})",
        )
    if amount_cents > AGENT_REFUND_LIMIT_CENTS:
        return tool_error(
            "business",
            f"Refund of ${amount_cents / 100:.2f} exceeds the agent limit of "
            f"${AGENT_REFUND_LIMIT_CENTS / 100:.2f}. Escalate to a human agent.",
            attempted=f"process_refund({order_id!r}, {amount_cents})",
            # Partial results survive the failure - the coordinator can still
            # use what was established before the rejection.
            partial={"order_verified": True, "eligible_amount_cents": amount_cents},
        )
    try:
        with _connect() as conn:
            cur = conn.execute(
                "UPDATE orders SET refunded = 1, status = 'refunded' "
                "WHERE order_id = ? AND refunded = 0",
                (order_id,),
            )
            changed = cur.rowcount
    except sqlite3.OperationalError as exc:
        return tool_error("transient", f"Database unavailable: {exc}",
                          retryable=True)

    if changed == 0:
        return tool_error(
            "business", "Order not found or already refunded.",
            attempted=f"process_refund({order_id!r}, {amount_cents})",
        )
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(
            {"refunded": True, "order_id": order_id,
             "amount_cents": amount_cents}))],
        is_error=False,
    )


# ---------------------------------------------------------------------------
# AN MCP RESOURCE. Exam point (2.4): resources expose a CATALOGUE so the agent
# can see what exists without burning turns on exploratory tool calls.
# ---------------------------------------------------------------------------
@server.resource("support://catalog/orders")
def order_catalog() -> str:
    """One-line summary of every order, so the agent knows what exists."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT order_id, status, total_cents FROM orders"
        ).fetchall()
    return json.dumps([dict(r) for r in rows], indent=2)


if __name__ == "__main__":
    server.run()          # stdio transport


# =============================================================================
# PART B - connecting to a REMOTE MCP server from the Messages API.
# This is real, current syntax (beta header mcp-client-2025-11-20).
# =============================================================================
#
# import anthropic
# client = anthropic.Anthropic()
#
# response = client.beta.messages.create(
#     model="claude-opus-5",
#     max_tokens=2048,
#     messages=[{"role": "user", "content": "What's blocking the release?"}],
#     mcp_servers=[{
#         "type": "url",                       # only "url" is supported
#         "url": "https://mcp.example.com/sse",
#         "name": "jira-mcp",
#         "authorization_token": os.environ["JIRA_MCP_TOKEN"],
#     }],
#     tools=[{
#         "type": "mcp_toolset",
#         "mcp_server_name": "jira-mcp",       # must match the server name
#         # ALLOWLIST: default off, then switch on only what this agent needs.
#         # This is the "4-5 tools, not 18" principle expressed in config.
#         "default_config": {"enabled": False},
#         "configs": {
#             "search_issues": {"enabled": True},
#             "get_issue":     {"enabled": True},
#         },
#     }],
#     betas=["mcp-client-2025-11-20"],
# )
#
# WHAT COMES BACK IS DIFFERENT FROM LOCAL TOOLS. There is no pause for you to
# execute anything - the server ran it. You get two new block types:
#
#   {"type": "mcp_tool_use",    "id": "mcptoolu_...", "name": "search_issues",
#    "server_name": "jira-mcp", "input": {...}}
#
#   {"type": "mcp_tool_result", "tool_use_id": "mcptoolu_...",
#    "is_error": false, "content": [{"type": "text", "text": "..."}]}
#
# for block in response.content:
#     if block.type == "mcp_tool_result" and block.is_error:
#         detail = json.loads(block.content[0].text)   # our structured error
#         if detail["isRetryable"]:
#             ...
#
# CONTRAST, and this is the exam's distinction:
#   local/client tool -> stop_reason "tool_use", YOU run it, YOU send tool_result
#   MCP tool          -> the server runs it; no stop for you
#   server tool       -> Anthropic runs it; may return stop_reason "pause_turn"
#
# =============================================================================
# CLAUDE CODE CONFIG - where this server goes.
#
# .mcp.json  (PROJECT scope: committed to git, shared with the team)
# {
#   "mcpServers": {
#     "support-tools": {
#       "command": "python3",
#       "args": ["02_mcp_server.py"],
#       "env": { "SUPPORT_DB_TOKEN": "${SUPPORT_DB_TOKEN}" }
#     }
#   }
# }
#   ${VAR} is expanded at load time, so the secret never enters the repo.
#
# ~/.claude.json  (USER scope: personal / experimental, NOT shared)
#   Same shape. If a teammate "isn't getting" a server, it was configured here.
# =============================================================================