"""
PSEUDOCODE -- illustrates the concept from your screenshot, not a real,
installable library.

I checked: no Anthropic library (raw `anthropic` SDK, or the Claude Agent
SDK's `claude_agent_sdk`) actually accepts a tool shaped like
{"type": "task", "name": "Task"} inside a "tools" list, or a top-level
"allowedTools" key on a plain config dict. Real equivalents:
  - raw `anthropic` SDK: no built-in Task tool at all -- you build the whole
    mechanism yourself (see coordinator_subagents_demo.py /
    task_tool_simple_demo.py in this folder).
  - Claude Agent SDK: has a REAL built-in delegation tool, but it's
    configured as `agents={...}` + `allowed_tools=["Agent"]` on
    ClaudeAgentOptions -- not as a "tools": [{"type": "task", ...}] entry
    (see agent_sdk_subagents_demo.py in this folder).

So this file is a conceptual walkthrough written in the same shape as your
screenshot -- plain dicts, no imports, nothing that calls a real API --
so you can map each piece to whichever real SDK you end up using.
"""

# ---------------------------------------------------------------------------
# 1. Coordinator agent configuration
#    (this is your screenshot, completed)
# ---------------------------------------------------------------------------
coordinator_config = {
    "model": "claude-haiku-4-5",   # cost-efficient for orchestration
    "system": """You are a research coordinator.
Decompose tasks and delegate to specialized subagents.
Specify research goals and quality criteria.
Do NOT provide step-by-step procedures.""",

    "tools": [
        # -- The Task tool enables subagent spawning --
        {
            "type": "task",        # conceptual marker: "this is the built-in delegation tool"
            "name": "Task",        # must be exactly "Task" (capital T)
        },
        # -- compile_report is an ordinary tool, unrelated to delegation --
        # it sits in the SAME "tools" list as Task, but it's just a normal
        # function the coordinator can call directly, with its own schema.
        {
            "type": "custom",
            "name": "compile_report",
            "description": "Combine subagent findings into one final report.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "sections": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "One string per subagent's findings.",
                    },
                },
                "required": ["sections"],
            },
        },
    ],

    # -- allowedTools MUST explicitly include "Task" --
    # Conceptually: "tools" is the roster of what EXISTS; "allowedTools" is
    # the roster of what's permitted to run WITHOUT a human approving each
    # call. Leaving "Task" out of allowedTools means every delegation
    # attempt would need manual sign-off before it could run.
    "allowedTools": ["Task", "compile_report"],
}


# ---------------------------------------------------------------------------
# 2. Subagent roster -- what "Task" is allowed to delegate to.
#    In your screenshot this lived off-screen; conceptually it's a second
#    config dict the harness consults whenever a Task call names a
#    subagent_type.
# ---------------------------------------------------------------------------
subagent_roster = {
    "researcher": {
        "model": "claude-sonnet-5",
        "system": "You are a research subagent. Investigate the assigned "
                   "question thoroughly and report findings with sources.",
        "tools": ["web_search", "web_fetch"],
    },
    "writer": {
        "model": "claude-sonnet-5",
        "system": "You are a writing subagent. Turn research findings into "
                   "clear, well-organized prose.",
        "tools": [],
    },
}


# ---------------------------------------------------------------------------
# 3. What Claude's RESPONSE looks like when it decides to delegate.
#    This is not something you write -- it's what the API would send back
#    to you, because "Task" is in allowedTools. block.name == "Task" and
#    block.input names which subagent_roster entry to spawn.
# ---------------------------------------------------------------------------
task_tool_call = {
    "type": "tool_use",
    "id": "toolu_01example",
    "name": "Task",
    "input": {
        "subagent_type": "researcher",
        "prompt": "Investigate current best practices for prompt caching "
                   "with the Anthropic API.",
    },
}


# ---------------------------------------------------------------------------
# 4. Conceptually, what the harness does with that tool_use block: look up
#    the named subagent in the roster, run a fresh conversation with ITS
#    system prompt, and return only the finished text -- the coordinator
#    never sees the subagent's intermediate steps.
# ---------------------------------------------------------------------------
def dispatch_task_call(call: dict, roster: dict) -> dict:
    subagent_type = call["input"]["subagent_type"]
    subagent_prompt = call["input"]["prompt"]
    config = roster[subagent_type]

    # In real code this line is "run a full agent loop using config['system']
    # and config['tools'], seeded with subagent_prompt, and capture its
    # final answer." Left as a placeholder here since this file has no API
    # calls at all.
    final_answer = f"<{subagent_type} subagent's finished answer to: {subagent_prompt!r}>"

    return {
        "type": "tool_result",
        "tool_use_id": call["id"],
        "content": final_answer,
    }


# ---------------------------------------------------------------------------
# 5. The tool_result you feed back to the coordinator -- same shape as any
#    other tool result, whether it came from Task or from compile_report.
# ---------------------------------------------------------------------------
task_tool_result = dispatch_task_call(task_tool_call, subagent_roster)


if __name__ == "__main__":
    print("coordinator_config['allowedTools'] ->", coordinator_config["allowedTools"])
    print("\nClaude asks to delegate:\n ", task_tool_call)
    print("\nHarness resolves it via subagent_roster and returns:\n ", task_tool_result)
