"""
=============================================================================
 RESEARCH SYSTEM - PART 1 of 2:  THE MCP SERVER
=============================================================================
 Scenario: a multi-agent research system. This half exposes the document
 corpus as MCP tools. Part 2 (research_system.py) runs the agentic loop and
 the coordinator.

   pip install "mcp[cli]"
   python3 corpus_mcp_server.py --build     # create + seed the corpus
   python3 corpus_mcp_server.py             # run as an MCP server (stdio)
=============================================================================
"""

# --- PYTHON: IMPORTS ---------------------------------------------------------
# `import json`            -> load the whole module, use it as `json.dumps(...)`
# `from x import y`        -> pull one name out, use it bare as `y`
# Both forms appear below. There is no functional difference, only naming.
import json
import sqlite3
import sys

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

# --- PYTHON: MODULE-LEVEL CONSTANTS -----------------------------------------
# Python has no `const` keyword. ALL_CAPS is a convention meaning "do not
# reassign this". Nothing enforces it; it is a message to other humans.
DB_PATH = "corpus.db"
MAX_EXCERPT_CHARS = 600

# --- PYTHON: CREATING AN OBJECT ---------------------------------------------
# MCPServer(...) calls the class and hands back an instance stored in `server`.
# Same shape as `x = list()` or `d = dict()` - classes are called like
# functions to build instances.
server = MCPServer("research-corpus")


# =============================================================================
# STRUCTURED ERRORS
# =============================================================================
# The single most important idea in tool design. A tool that returns
# "Operation failed" tells the agent nothing it can act on. It cannot tell
# "retry in two seconds" from "never retry, escalate to a human".
#
# Four categories:
#   transient   timeout, lock, 503        -> retryable
#   validation  malformed input           -> fix the input, then retry
#   business    policy violation          -> escalate, never retry
#   permission  missing auth or scope     -> escalate, never retry

def tool_error(
    category: str,
    message: str,
    *,                          # see PYTHON note below
    retryable: bool = False,
    attempted: str = "",
    partial: object = None,
) -> CallToolResult:
    """Build an MCP error result an agent can reason about.

    PYTHON: THE BARE `*` IN A PARAMETER LIST
      Everything AFTER the `*` must be passed by name, not by position.
      Legal:   tool_error("business", "over limit", retryable=False)
      Illegal: tool_error("business", "over limit", False)
      This stops silent bugs where someone passes arguments in the wrong order.

    PYTHON: DEFAULT VALUES  (`retryable: bool = False`)
      If the caller omits it, False is used. Callers only specify what differs.

    PYTHON: THE RETURN TYPE HINT  (`-> CallToolResult`)
      Documentation for humans and editors. Python does not check or enforce
      it at runtime - a function hinted `-> int` can still return a string.
    """
    body = {
        "errorCategory": category,
        "isRetryable": retryable,
        "message": message,
        "attempted": attempted,
        "partialResults": partial,
    }
    return CallToolResult(
        # json.dumps turns a Python dict into a JSON string.
        # json.loads goes the other way. Mnemonic: dumpS = dump to String.
        content=[TextContent(type="text", text=json.dumps(body))],
        is_error=True,          # serialises to `isError` on the MCP wire
    )


def tool_ok(payload: dict) -> CallToolResult:
    """Success counterpart. Note is_error=False, not the absence of the field."""
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        is_error=False,
    )


# =============================================================================
# THE CORPUS
# =============================================================================
def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=2.0)
    # Without this line a row is a plain tuple: row[0], row[1]...
    # With it, a row behaves like a dict: row["title"]. Far less error-prone.
    conn.row_factory = sqlite3.Row
    return conn


def build_corpus() -> None:
    """Create and seed a real database. Run once with --build."""
    conn = sqlite3.connect(DB_PATH)

    # PYTHON: TRIPLE-QUOTED STRINGS
    # """...""" spans multiple lines and keeps the line breaks. Used here for
    # SQL, and above for docstrings.
    conn.executescript("""
        DROP TABLE IF EXISTS documents;
        CREATE TABLE documents (
            doc_id      TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            publisher   TEXT NOT NULL,
            published   TEXT NOT NULL,      -- YYYY-MM-DD
            topic       TEXT NOT NULL,
            body        TEXT NOT NULL,
            access      TEXT NOT NULL       -- 'open' or 'restricted'
        );
    """)

    # PYTHON: A LIST OF TUPLES
    #   [ ... ]  is a list  - ordered, changeable
    #   ( ... )  is a tuple - ordered, FIXED once created
    # Rows are tuples because a row's shape never changes.
    rows = [
        ("DOC-001", "Global Energy Storage Outlook 2026", "IEA", "2026-04-02",
         "manufacturing",
         "Global sodium-ion cell manufacturing capacity reached 4.2 GWh in "
         "2025, concentrated in three Chinese facilities. Cost per kWh fell "
         "to $61, down from $87 in 2024.", "open"),

        ("DOC-002", "Datang 50MWh Sodium-Ion Pilot: Operating Report",
         "Datang Group", "2026-03-14", "grid_integration",
         "The installation retained existing PCS hardware without inverter "
         "modification. Frequency response was within 0.2s of specification "
         "across 400 cycles.", "open"),

        ("DOC-003", "Interconnection Queue Analysis Q1 2026", "ERCOT",
         "2026-02-20", "grid_integration",
         "Sodium-ion projects face identical interconnection timelines to "
         "lithium-ion. Median queue duration was 41 months.", "open"),

        ("DOC-004", "EU Battery Regulation: Chemistry-Neutral Provisions",
         "European Commission", "2026-01-30", "regulatory",
         "RESTRICTED CONTENT", "restricted"),

        ("DOC-005", "Sodium-Ion Cost Curve Projections to 2030",
         "BloombergNEF", "2025-11-08", "economics",
         "Projections place sodium-ion at $48/kWh by 2028 under a "
         "high-deployment scenario. A competing analysis from Wood Mackenzie "
         "places the same figure at $55/kWh.", "open"),
    ]

    # `executemany` runs the same SQL once per tuple. The ? marks are
    # placeholders - NEVER build SQL with f-strings, that is SQL injection.
    conn.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    print(f"built {DB_PATH} with {len(rows)} documents")
    # PYTHON: len(x) gives the number of items in a list, dict, or string.


# =============================================================================
# THE TOOLS
# =============================================================================
# PYTHON: DECORATORS
#   The `@server.tool()` line above a function is a DECORATOR. It hands the
#   function to `server.tool()`, which registers it as an MCP tool. The
#   function still works normally when you call it yourself - which is why the
#   test at the bottom of this file can invoke it directly.
#
# The DOCSTRING becomes the tool description the model reads. So it is written
# for a model deciding whether to call this tool, not for a maintainer.

@server.tool()
def search_corpus(topic: str, published_after: str = "") -> CallToolResult:
    """Search the research corpus for documents on a topic.

    Use this to discover what evidence exists before reading anything in full.
    Do not use it to read a document - call fetch_document for that.

    topic must be one of: manufacturing, grid_integration, regulatory,
    economics. published_after is optional, format YYYY-MM-DD.

    Returns an array of matches with doc_id, title, publisher, published, and
    access. A search that matches nothing returns an empty array with
    found=0 - that is a successful search, not a failure, and must not be
    retried.
    """
    VALID = {"manufacturing", "grid_integration", "regulatory", "economics"}
    # PYTHON: `{...}` with bare values (no colons) is a SET - unordered, unique
    # members, and `x in some_set` is very fast. With colons it is a dict.

    if topic not in VALID:
        return tool_error(
            "validation",
            # PYTHON: sorted() returns a sorted list; ", ".join(list) glues the
            # items into one string separated by ", ".
            f"Unknown topic {topic!r}. Valid topics: {', '.join(sorted(VALID))}.",
            attempted=f"search_corpus(topic={topic!r})",
        )
        # PYTHON: {topic!r} inside an f-string calls repr() - it adds quotes,
        # so 'grid ' is visibly different from 'grid'. Catches whitespace bugs.

    sql = "SELECT doc_id,title,publisher,published,access FROM documents WHERE topic = ?"
    params = [topic]           # a list we may append to
    if published_after:
        # PYTHON: TRUTHINESS. An empty string is "falsy", so `if published_after`
        # means "if it is not empty". Same for [], {}, 0, and None.
        sql += " AND published >= ?"
        params.append(published_after)

    try:
        # PYTHON: `with ... as x:` is a CONTEXT MANAGER. It guarantees cleanup
        # (commit/rollback here) even if an exception is raised inside.
        with connect() as conn:
            rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        # PYTHON: try/except. Code that might fail goes in `try`. If the named
        # exception occurs, `except` runs instead of the program crashing.
        # `as exc` binds the exception object so you can read its message.
        return tool_error(
            "transient", f"Corpus unavailable: {exc}",
            retryable=True,                 # a lock genuinely clears on retry
            attempted=f"search_corpus(topic={topic!r})",
        )

    # PYTHON: LIST COMPREHENSION
    #   [dict(r) for r in rows]
    # reads as "make a list containing dict(r), for each r in rows". It is a
    # compact for-loop. The long form is:
    #   out = []
    #   for r in rows:
    #       out.append(dict(r))
    matches = [dict(r) for r in rows]

    # An empty result is SUCCESS. This distinction is heavily tested: an agent
    # must not retry a search that legitimately found nothing.
    return tool_ok({"found": len(matches), "documents": matches})


@server.tool()
def fetch_document(doc_id: str) -> CallToolResult:
    """Retrieve the full text of one corpus document.

    Use after search_corpus has identified a doc_id worth reading. Do not
    guess doc_ids.

    Returns doc_id, title, publisher, published, and body. Documents marked
    access='restricted' return errorCategory 'permission' with isRetryable
    false - report these as coverage gaps rather than retrying.
    """
    if not doc_id.startswith("DOC-"):
        return tool_error(
            "validation", "doc_id must be formatted DOC-001.",
            attempted=f"fetch_document({doc_id!r})",
        )

    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            # PYTHON: `(doc_id,)` - the trailing comma makes it a one-item
            # TUPLE. `(doc_id)` without the comma is just the string in
            # brackets, and sqlite3 would reject it.
    except sqlite3.OperationalError as exc:
        return tool_error("transient", f"Corpus unavailable: {exc}",
                          retryable=True)

    if row is None:
        # Not found via a well-formed id = a valid empty answer, not an error.
        return tool_ok({"found": False, "doc_id": doc_id})

    if row["access"] == "restricted":
        return tool_error(
            "permission",
            f"{doc_id} is access-restricted and cannot be read by this agent. "
            f"Report it as a coverage gap; do not retry.",
            attempted=f"fetch_document({doc_id!r})",
            # PARTIAL RESULTS survive the failure. The metadata is still
            # usable even though the body is not - the agent can cite the
            # document's existence and date.
            partial={"doc_id": doc_id, "title": row["title"],
                     "publisher": row["publisher"], "published": row["published"]},
        )

    return tool_ok({
        "found": True,
        "doc_id": row["doc_id"],
        "title": row["title"],
        "publisher": row["publisher"],
        "published": row["published"],       # provenance metadata, always
        "body": row["body"][:MAX_EXCERPT_CHARS],
        # PYTHON: SLICING. s[:600] means "characters 0 up to 600". s[2:5] is
        # 2,3,4. s[-1] is the last character. Trimming here keeps tool results
        # from bloating the agent's context on every turn.
    })


# =============================================================================
# AN MCP RESOURCE
# =============================================================================
# Tools are ACTIONS the agent chooses to take. A resource is CONTEXT the agent
# can be given. Exposing a catalogue as a resource lets the agent see what
# exists without burning turns on exploratory tool calls.
@server.resource("corpus://catalog")
def catalog() -> str:
    """One line per document, so the agent knows the shape of the corpus."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT doc_id,title,topic,published,access FROM documents"
        ).fetchall()
    return json.dumps([dict(r) for r in rows], indent=2)


# =============================================================================
# ENTRY POINT
# =============================================================================
# PYTHON: `if __name__ == "__main__":`
#   True when this file is RUN directly, False when it is IMPORTED by another
#   file. Part 2 imports the tool functions from here; this guard stops the
#   server from starting up when that happens.
if __name__ == "__main__":
    # sys.argv is the list of command-line words. sys.argv[0] is the filename.
    if "--build" in sys.argv:
        build_corpus()
    else:
        server.run()          # serve over stdio
