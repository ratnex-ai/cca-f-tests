"""
The Task/Agent tool: the BUILT-IN delegation tool in the Claude Agent SDK.

This is a different library from the other two demo files in this folder.
coordinator_subagents_demo.py and task_tool_simple_demo.py both hand-roll
everything against the raw Anthropic Messages API (`pip install anthropic`)
-- you write the tool's JSON schema yourself, because nothing like it exists
until you build it.

The Claude Agent SDK (`pip install claude-agent-sdk`) is different: it IS
the Claude Code harness, packaged as a library. The delegation tool already
exists inside it. You never define its schema. You only:

  1. describe the subagents it's allowed to dispatch to (`agents=`), and
  2. put the tool's name in `allowed_tools` so it can be called without a
     permission prompt.

That second point is exactly what your screenshot's

    "tools": [{"type": "task", "name": "Task"}],
    "allowedTools": ["Task", "compile_report"]

was showing -- "allowedTools MUST explicitly include Task". In current SDK
releases this tool is named "Agent" (renamed from "Task" in Claude Code
v2.1.63 -- some places, like permission_denials[].tool_name, can still say
"Task"). Below uses "Agent", the current name.

Setup: pip install claude-agent-sdk
"""

import asyncio
from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AgentDefinition,
    ToolUseBlock,
)

# ---------------------------------------------------------------------------
# 1. Subagent definitions. Each is a real AgentDefinition: its own system
#    prompt (`prompt`), its own restricted toolset (`tools`), and a
#    `description` the coordinator reads to decide when to delegate to it.
#    This is the SDK's equivalent of the roster you'd hand-build yourself
#    in task_tool_simple_demo.py's SUBAGENTS dict.
# ---------------------------------------------------------------------------
AGENTS = {
    "researcher": AgentDefinition(
        description="Answers factual questions by reading or searching files. "
                     "Use for lookups, not creative writing.",
        prompt="You are a research assistant. Answer factual questions "
               "concisely, in one sentence.",
        tools=["Read", "Grep", "Glob"],   # read-only -- can't edit or run commands
    ),
    "poet": AgentDefinition(
        description="Writes short poems. Use for creative writing requests.",
        prompt="You are a poet. Write short, vivid poems. Output only the "
               "poem -- no preamble, no explanation.",
        tools=[],   # pure text generation -- no tools needed
    ),
}


# ---------------------------------------------------------------------------
# 2. Run the coordinator. This is the direct equivalent of your screenshot's
#    coordinator_config:
#      "tools": [{"type": "task", "name": "Task"}]   -> here, just agents=AGENTS
#      "allowedTools": ["Task", ...]                  -> here, "Agent" in allowed_tools
#    Nothing else is needed to enable delegation -- the SDK supplies the
#    Task/Agent tool itself; you only supply what it can delegate to and
#    permission to call it.
# ---------------------------------------------------------------------------
async def main():
    async for message in query(
        prompt="Use the researcher agent to find out the capital of France, "
               "then use the poet agent to write a two-line poem about that city.",
        options=ClaudeAgentOptions(
            allowed_tools=["Agent"],   # <-- MUST be here, or delegation calls get denied
            agents=AGENTS,
        ),
    ):
        # Detect delegation: a tool_use block named "Agent" (or "Task" on
        # older SDK releases) is the coordinator spawning a subagent.
        # block.input["subagent_type"] says which one.
        if hasattr(message, "content") and message.content:
            for block in message.content:
                if isinstance(block, ToolUseBlock) and block.name in ("Task", "Agent"):
                    print(f"-- delegating to: {block.input.get('subagent_type')}")

        if hasattr(message, "result"):
            print("\n=== Final answer ===")
            print(message.result)


if __name__ == "__main__":
    asyncio.run(main())
