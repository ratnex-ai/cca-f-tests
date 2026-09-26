"""
Same agent as agentic_loop_demo.py, but the "brain" is now the real
Claude API instead of a hand-written rule-based planner. Claude itself
decides which tool to call and when it's done, via its actual
stop_reason / tool_use response fields.

Setup:
    pip install anthropic
    PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."
    then:        python agentic_loop_claude_api.py

PYTHON: comments here only cover what's new compared to agentic_loop_demo.py
(the API call, message history, and tool-result plumbing) — see that file
for the general Python syntax primer.
"""

import os
import anthropic

MODEL = "claude-sonnet-5"


# ---------------------------------------------------------------------------
# 1. Tools: identical functions to agentic_loop_demo.py. Claude never runs
#    this code itself — it only ever asks *us* to run it and reads back
#    whatever we return.
# ---------------------------------------------------------------------------

def calculator(expression: str) -> str:
    allowed_chars = set("0123456789+-*/.() ")
    if not set(expression) <= allowed_chars:
        return "error: expression contains disallowed characters"
    return str(eval(expression, {"__builtins__": {}}))


KNOWLEDGE_BASE = {
    "average restaurant tip percent": "18",
    "capital of france": "Paris",
    "boiling point of water celsius": "100",
}

def knowledge_lookup(query: str) -> str:
    return KNOWLEDGE_BASE.get(query.strip().lower(), "unknown")


TOOLS = {
    "calculator": calculator,
    "knowledge_lookup": knowledge_lookup,
}

# Claude doesn't see the Python functions above — it only sees this schema.
# name/description/input_schema together are Claude's *entire* understanding
# of what each tool does. It uses the description to decide WHEN to call
# a tool, so write it like documentation for a new teammate, not a code
# comment. input_schema is plain JSON Schema, same as MCP tool schemas.
TOOL_SCHEMAS = [
    {
        "name": "calculator",
        "description": (
            "Evaluate a basic arithmetic expression (+, -, *, /, parentheses) "
            "and return the numeric result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "A math expression, e.g. '84 * 0.15'",
                }
            },
            "required": ["expression"],
        },
    },
    {
        "name": "knowledge_lookup",
        "description": (
            "Look up a known fact by name, e.g. 'capital of france' or "
            "'average restaurant tip percent'. Returns 'unknown' if the "
            "fact isn't in the knowledge base."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The fact to look up, in plain English.",
                }
            },
            "required": ["query"],
        },
    },
]


# ---------------------------------------------------------------------------
# 2. The agentic loop: ask Claude, run any tools it asks for, send the
#    results back, repeat until Claude's stop_reason is "end_turn".
# ---------------------------------------------------------------------------

def run_agent(goal: str, max_iterations: int = 6) -> str:
    # Reads the ANTHROPIC_API_KEY environment variable automatically —
    # no key is written in this file.
    client = anthropic.Anthropic()

    # PYTHON: `messages` is a list of dicts we build up ourselves — this
    # *is* the conversation. Claude is stateless between API calls: every
    # call resends the whole history so far, which is how it "remembers"
    # its own earlier tool calls and their results within one run_agent()
    # call. We start it off with the user's goal as the first turn.
    messages = [{"role": "user", "content": goal}]
    print(f"\n=== Goal: {goal} ===")

    # PYTHON: `for step in range(max_iterations)` counts step from 0 up to
    # max_iterations - 1, then stops on its own — a bounded loop instead
    # of the `while` + manual increment used in the mock version. Same
    # safety purpose: a misbehaving conversation can't loop forever.
    for step in range(max_iterations):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        # Append Claude's own reply to the history before deciding what to
        # do next — its next turn needs to see what it just said, exactly
        # like a real back-and forth conversation transcript.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # response.content is a list of blocks; a plain text answer
            # comes back as one or more blocks of type "text". Joining
            # them handles the (uncommon) case where Claude splits its
            # reply across more than one text block.
            final_text = "".join(
                block.text for block in response.content if block.type == "text"
            )
            print(f"[step {step}] final answer -> {final_text}")
            return final_text

        if response.stop_reason == "tool_use":
            # A single turn can request more than one tool at once, so we
            # walk every block, run the ones that are tool_use requests,
            # and collect one tool_result per request.
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                print(f"[step {step}] thought: need {block.name}({block.input})")

                tool_fn = TOOLS[block.name]
                # PYTHON: `**block.input` unpacks a dict into keyword
                # arguments. block.input is e.g. {"expression": "84*0.15"},
                # so `tool_fn(**block.input)` calls calculator(expression="84*0.15") —
                # this works because our schema's parameter names match the
                # Python functions' parameter names exactly.
                result = tool_fn(**block.input)
                print(f"[step {step}] observation: {result}")

                # tool_use_id links this result back to the specific
                # tool_use request it answers — required so Claude can
                # match results to calls when several were requested at once.
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            # Tool results go back as a "user" turn — from the API's point
            # of view, "here are the tool outputs" is just the next thing
            # the human/app side of the conversation says.
            messages.append({"role": "user", "content": tool_results})
            continue  # PYTHON: skip straight to the next loop iteration

        raise RuntimeError(f"unexpected stop_reason: {response.stop_reason}")

    raise RuntimeError("agent did not converge within max_iterations")


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "Set ANTHROPIC_API_KEY first, e.g. (PowerShell):\n"
            '  $env:ANTHROPIC_API_KEY = "sk-ant-..."'
        )
    run_agent("How much is a 15% tip on an $84 bill, and is that above or below average?")
    run_agent("What's the capital of France?")
