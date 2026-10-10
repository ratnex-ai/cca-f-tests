"""
06 - SAME TASK, TWO LAYERS: query() vs. messages.create()  (CCA-F Domain 1.1)
=============================================================================
One task - "find the TODO comments in this repo's Python files and summarise
them" - done twice:

  PART A - anthropic  client.messages.create()   (Messages API, raw layer)
  PART B - claude_agent_sdk  query()             (Agent SDK, agent layer)

Both parts spell out the exam-relevant properties explicitly - on the tool
definition, on messages.create(), and on ClaudeAgentOptions - with a comment
on WHY each one matters. Real code would leave many of these at defaults.

    pip install anthropic claude-agent-sdk
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 06_query_vs_messages_create.py

WHERE EACH ONE IS USEFUL
-----------------------------------------------------------------------------
messages.create()  - ONE request -> ONE response. You own everything else.
  Use it when:
  * the job is a single call: classify, extract, summarise, translate,
    LLM-as-judge - no tools, no loop needed
  * you need exact control of every request: custom tools that hit your own
    systems, your own retry/validation logic, output_config.format, prompt
    caching breakpoints, per-turn cost accounting
  * you're embedding Claude in an existing backend (a web request handler, a
    batch pipeline) where you don't want a filesystem-capable agent at all
  Cost of choosing it: YOU write the agentic loop, the tool executor, the
  history management, the turn/budget guardrails.

query()  - ONE prompt -> a whole autonomous agent run (many turns, tools,
  subagents), streamed back as messages.
  Use it when:
  * the task is open-ended and multi-step: "investigate", "fix", "audit",
    "research" - you can't predict how many tool calls it takes
  * you want Claude Code's built-in tools (Read, Grep, Glob, Edit, Bash,
    WebSearch, Agent) instead of writing your own
  * you want subagents (AgentDefinition), hooks, permissions, CLAUDE.md /
    skills via setting_sources, and max_turns / max_budget_usd enforced FOR you
  Cost of choosing it: less control per turn; the agent picks its own steps
  inside the limits you set (allowed_tools, permission_mode, hooks).

EXAM TAKEAWAY: the decision is about WHO OWNS THE LOOP. Pick messages.create()
when you need to own it (or there is no loop); pick query() when the loop,
tools and guardrails are boilerplate you'd only be re-implementing.
=============================================================================
"""

import asyncio
import json
import os
from pathlib import Path

import anthropic

REPO = Path(__file__).parent
TASK = ("Find the TODO comments in this repo's top-level .py files and give "
        "me a 3-bullet summary of what is still unfinished.")


# =============================================================================
# SHARED - the tool's behaviour. PART A exposes it as a Messages API tool,
# PART B as an in-process MCP tool. Same function, two wrappings.
# =============================================================================
def grep_markers(marker: str = "TODO", file_glob: str = "*.py",
                 max_results: int = 50) -> list[dict]:
    # Your code, your sandbox: the model can only reach what this function
    # chooses to expose - REPO only, read-only, capped output.
    hits = []
    for path in sorted(REPO.glob(file_glob)):
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if marker in line:
                hits.append({"file": path.name, "line": n, "text": line.strip()})
                if len(hits) >= max_results:
                    return hits
    return hits


# =============================================================================
# PART A - messages.create(): you supply the tools AND the loop
# =============================================================================
# The Messages API has no filesystem. If Claude needs to look at files, you
# define a tool, execute it yourself, and feed the result back. Every line of
# the loop below is something query() does for you in PART B.

TOOLS = [
    {
        # name - what Claude emits in tool_use.name and what you dispatch on.
        # ^[a-zA-Z0-9_-]{1,64}$. Verb_noun names reduce mis-selection between
        # similar tools.
        "name": "grep_markers",

        # description - the single biggest lever on tool SELECTION. Say what
        # it does, WHEN to use it, when NOT to, what it returns, and its
        # limits. A one-liner like "Searches files" is the classic exam trap.
        "description": (
            "Search this repository's files for code-comment markers such as "
            "TODO or FIXME and return every matching line. Use this whenever "
            "the user asks what is unfinished, pending, or flagged in the "
            "code. Do NOT use it to read whole files or search for arbitrary "
            "text. Returns a JSON list of {file, line, text}; an empty list "
            "means no matches. Read-only; searches top-level files only."
        ),

        # input_schema - JSON Schema for the arguments.
        "input_schema": {
            "type": "object",
            "properties": {
                "marker": {
                    "type": "string",
                    # enum - constrains the value instead of hoping the
                    # description is followed.
                    "enum": ["TODO", "FIXME", "HACK", "XXX"],
                    "description": "Comment marker to search for. Default TODO.",
                },
                "file_glob": {
                    "type": "string",
                    "description": "Glob for files to search, e.g. '*.py'.",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Cap on returned matches. Keep it small - "
                                   "results go into the context window.",
                },
            },
            # required - which args Claude MUST send; the rest are optional.
            "required": ["marker"],
            # additionalProperties: false - reject invented arguments.
            # Required (with `required`) for strict mode below.
            "additionalProperties": False,
        },

        # strict - GUARANTEES tool_use.input matches input_schema (structured
        # outputs). Without it the schema is followed best-effort only.
        "strict": True,

        # cache_control on the LAST tool caches the whole tools array as the
        # start of the prompt prefix. (This one tool is under the minimum
        # cacheable size, so here it silently won't cache - no error.)
        "cache_control": {"type": "ephemeral"},
    },
    # A SERVER tool, for contrast: `type` instead of input_schema, and
    # Anthropic executes it - no tool_result from you. Uncomment to enable.
    # {"type": "web_search_20250305", "name": "web_search", "max_uses": 3,
    #  "allowed_domains": ["docs.python.org"]},
]


def run_with_messages_api(max_turns: int = 5) -> str:
    client = anthropic.Anthropic(
        max_retries=3,     # SDK retries 429/5xx with backoff - transient errors
        timeout=60.0,      # seconds; a hung request must not hang the loop
    )
    messages = [{"role": "user", "content": TASK}]

    for _ in range(max_turns):                 # YOUR turn guardrail
        response = client.messages.create(     # ONE request, ONE response
            model="claude-sonnet-5-5",

            # Caps THIS response's output only - not the cost of the run.
            max_tokens=1024,

            # system as a list of blocks (not a string) so it can carry a
            # cache breakpoint. Stable content first, varying content last.
            system=[{
                "type": "text",
                "text": ("You are a code-review assistant. Use tools to gather "
                         "facts before answering. Cite file:line for every "
                         "point you make."),
                "cache_control": {"type": "ephemeral"},
            }],

            messages=messages,                 # FULL history - API is stateless
            tools=TOOLS,

            # tool_choice options:
            #   {"type": "auto"}                 - Claude decides (default)
            #   {"type": "any"}                  - must call SOME tool
            #   {"type": "tool", "name": "x"}    - must call tool x
            #   {"type": "none"}                 - must not call tools
            # Forcing "any"/"tool" inside a loop means it can never return
            # end_turn - only force on a single extraction call.
            tool_choice={
                "type": "auto",
                # False = Claude may emit several tool_use blocks in one turn
                # (parallel tool use); you must answer ALL of them.
                "disable_parallel_tool_use": False,
            },

            # Low temperature for factual, repeatable tool use. (Recent
            # models reject setting temperature AND top_p together.)
            temperature=0.0,

            # Opaque end-user id for Anthropic's abuse detection. Never PII.
            metadata={"user_id": "user-7f3a"},

            # Extended thinking would go here. With thinking on, tool_choice
            # must be "auto" or "none" - forced tool use is rejected:
            # thinking={"type": "adaptive"},
        )
        messages.append({"role": "assistant", "content": response.content})

        # stop_reason is the ONLY loop signal. Not "the text looks finished".
        if response.stop_reason == "end_turn":
            return "".join(b.text for b in response.content if b.type == "text")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("output truncated - raise max_tokens")
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined the request")
        if response.stop_reason == "pause_turn":
            continue          # server tool paused a long turn: just resend
        # else stop_reason == "tool_use"

        results = []                            # YOUR tool executor
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name == "grep_markers":
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,    # must match the tool_use id
                    "content": json.dumps(grep_markers(**block.input)),
                })
            else:
                # Unknown tool: tell Claude, don't crash or silently skip.
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "is_error": True,
                    "content": f"Unknown tool {block.name!r}.",
                })
        # Every tool_result in ONE user message, right after the tool_use.
        messages.append({"role": "user", "content": results})

        print(f"   [api] usage: in={response.usage.input_tokens} "
              f"out={response.usage.output_tokens} "
              f"cache_read={response.usage.cache_read_input_tokens}")

    raise RuntimeError(f"guardrail: exceeded {max_turns} turns")


def classify_single_call(text: str) -> str:
    # The case where messages.create() is clearly the right layer: one
    # request, no tools, no loop. Spinning up an agent for this would add
    # latency, cost and a filesystem-capable process for nothing.
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",     # cheapest model that's good enough
        max_tokens=10,
        system="Reply with exactly one word: bug, feature, or docs.",
        messages=[{"role": "user", "content": text}],
        temperature=0.0,                       # same input -> same label
        stop_sequences=["\n"],                 # stop after the one-word answer
    )
    # stop_reason is "stop_sequence" if "\n" hit, else "end_turn".
    return response.content[0].text.strip()


# =============================================================================
# PART B - query(): the SDK owns the loop, the tools and the guardrails
# =============================================================================
# No stop_reason branching, no history list, no tool_result plumbing.
# You describe the LIMITS and consume the stream.

async def run_with_agent_sdk() -> str:
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultMessage,
        SystemMessage, TextBlock, ToolUseBlock, create_sdk_mcp_server, query,
        tool,
    )

    # --- Custom tool, SDK style. Same name / description / input_schema
    # trio as PART A, but declared with @tool and served by an in-process MCP
    # server. Claude sees it as  mcp__<server>__<tool>.
    @tool(
        "grep_markers",
        TOOLS[0]["description"],               # same description discipline
        {
            "type": "object",
            "properties": TOOLS[0]["input_schema"]["properties"],
            "required": ["marker"],
        },
    )
    async def grep_markers_tool(args: dict) -> dict:
        hits = grep_markers(**args)
        # MCP result shape. is_error=True would let Claude see and recover
        # from a failure instead of the run crashing.
        return {"content": [{"type": "text", "text": json.dumps(hits)}],
                "is_error": False}

    repo_server = create_sdk_mcp_server(
        name="repo", version="1.0.0", tools=[grep_markers_tool])

    # --- A PreToolUse hook: deterministic code, not a prompt rule. Blocks
    # reading secrets even if the model decides to try.
    async def block_secret_reads(input_data, tool_use_id, context):
        path = input_data.get("tool_input", {}).get("file_path", "")
        if path.endswith(".env") or "secrets" in path:
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Secrets are off-limits.",
            }}
        return {}

    options = ClaudeAgentOptions(
        # Claude Code's own system prompt + your additions. A plain string
        # here would REPLACE it entirely (losing tool-use guidance).
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": "Cite file:line for every point you make.",
        },
        model="sonnet",
        fallback_model="haiku",          # used if the primary is unavailable
        cwd=str(REPO),                   # the agent's working directory

        # The custom tool must be REGISTERED (mcp_servers) and ALLOWED
        # (allowed_tools, by its mcp__server__tool name).
        mcp_servers={"repo": repo_server},

        # allowed_tools = auto-approved without a permission prompt.
        allowed_tools=["Glob", "Grep", "Read", "mcp__repo__grep_markers"],
        # disallowed_tools = removed from the model's toolset entirely. This
        # is the architectural limit; a prompt saying "don't edit" is not.
        disallowed_tools=["Bash", "Write", "Edit", "WebFetch", "WebSearch"],

        # "default"           - anything not in allowed_tools needs approval
        # "acceptEdits"       - auto-approve file edits
        # "plan"              - analyse and propose only, no execution
        # "bypassPermissions" - approve everything (sandboxes only)
        permission_mode="default",

        hooks={"PreToolUse": [HookMatcher(matcher="Read",
                                          hooks=[block_secret_reads])]},

        # Load .claude/ from the project: CLAUDE.md, rules, skills. Without
        # it, the SDK does NOT pick up project config.
        setting_sources=["project"],

        max_turns=10,           # runaway guardrail, enforced by the SDK
        max_budget_usd=0.50,    # dollar guardrail - no Messages API equivalent

        # Session control (not used here):
        # resume="<session_id>"   - continue the same task's session
        # fork_session=True       - branch it to try an alternative approach
    )

    final = ""
    async for message in query(prompt=TASK, options=options):
        # The stream shows the agent's own steps - you observe the loop
        # rather than drive it.
        if isinstance(message, SystemMessage) and message.subtype == "init":
            print(f"   [agent] session={message.data.get('session_id')}")
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    print(f"   [agent] {block.name}({block.input})")
                elif isinstance(block, TextBlock):
                    final = block.text
        elif isinstance(message, ResultMessage):
            print(f"   [agent] done: subtype={message.subtype} "
                  f"turns={message.num_turns} "
                  f"cost=${message.total_cost_usd:.4f} "
                  f"session={message.session_id}")
            if message.subtype != "success":
                # error_max_turns / error_max_budget_usd: a guardrail stopped
                # it - report as partial, never as a completed answer.
                return f"[INCOMPLETE: {message.subtype}] {final}"
            return message.result or final
    return final


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("set ANTHROPIC_API_KEY first")

    print("=== PART A: messages.create() - single call, no loop ===")
    print(classify_single_call("The export button crashes on empty tables"))

    print("\n=== PART A: messages.create() - hand-written loop ===")
    print(run_with_messages_api())

    print("\n=== PART B: query() - SDK-owned agent loop ===")
    print(asyncio.run(run_with_agent_sdk()))
