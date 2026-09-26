"""
Minimal agentic loop: an agent that plans, calls tools, observes the
results, and repeats until it has a final answer.

This mirrors how Claude's tool use actually works in production:

    assistant emits a tool_use request
        -> your app executes the tool
        -> your app sends back a tool_result
        -> assistant continues (or stops)
        -> repeat until the assistant answers without requesting a tool

Here the "brain" that decides what to do next is a small rule-based
planner instead of a real LLM call, so the whole thing runs with no
API key and no dependencies. Swap decide_next_action() for an actual
Claude API call and the loop around it doesn't need to change shape.

Terminology note — this demo's action["type"] values are stand-ins for
real fields on Claude's API response:
  - "tool_call"    stands in for stop_reason == "tool_use": the response
                    contains a content block of type "tool_use" (the
                    model wants a tool run before it can continue).
  - "final_answer" stands in for stop_reason == "end_turn": the model
                    is done and produced a plain text answer, no tool
                    needed. This is the loop's termination condition.

This file is also written to double as a light Python tutorial: look
for the "PYTHON:" comments — those explain a language feature the
first time it shows up, not the agent logic itself.
"""

# PYTHON: `import` pulls in code that already exists elsewhere so you don't
# have to write it yourself. `from X import Y, Z` grabs specific names out
# of module X instead of the whole module.
#   - dataclass/field come from the standard library's `dataclasses` module.
#   - Callable is just a *type hint* (see below) meaning "a function".
from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# 1. Tools: the actions the agent is allowed to take in the world.
#    Same shape as a Claude/MCP tool: a name, and a function that takes
#    a string input and returns a string result.
# ---------------------------------------------------------------------------

# PYTHON: `def name(param: type) -> type:` defines a function.
# The `: str` and `-> str` are *type hints* — they document what kind of
# value goes in and comes out. Python doesn't enforce them at runtime
# (they're just documentation/tooling), but they make code much easier
# to read, and tools like mypy can check them for you.
def calculator(expression: str) -> str:
    # PYTHON: `{...}` with no colons after the values is a *set literal* —
    # an unordered collection of unique items. Here it's every character
    # we consider safe in a math expression.
    allowed_chars = set("0123456789+-*/.() ")

    # PYTHON: `set(expression)` turns the input string into a set of its
    # unique characters. `<=` between two sets means "is a subset of" —
    # i.e. "every character in expression is also in allowed_chars".
    # This is a safety check before we hand the string to eval().
    if not set(expression) <= allowed_chars:
        return "error: expression contains disallowed characters"

    # PYTHON: eval() runs a string as Python code and returns the result.
    # It's dangerous in general (it would happily run arbitrary code), so
    # we pass `{"__builtins__": {}}` as its environment to strip out
    # access to things like open(), import, etc. The character allowlist
    # above is a second layer of the same defense.
    # str(...) converts the numeric result (e.g. 12.6) into text, because
    # this function's contract is "always returns a string".
    return str(eval(expression, {"__builtins__": {}}))


# PYTHON: `{}` with `key: value` pairs inside is a *dict* (dictionary) —
# a lookup table from keys to values. This one is defined at module level
# (outside any function), so it acts like a small constant database that
# any function below can read.
KNOWLEDGE_BASE = {
    "average restaurant tip percent": "18",
    "capital of france": "Paris",
    "boiling point of water celsius": "100",
}

def knowledge_lookup(query: str) -> str:
    # PYTHON: `"some text".strip()` removes leading/trailing whitespace;
    # `.lower()` lowercases it. Chaining `.strip().lower()` runs them
    # left to right, so lookups aren't broken by stray spaces or case.
    # PYTHON: `dict.get(key, default)` looks up `key` and returns
    # `default` instead of crashing if the key isn't found — safer than
    # `dict[key]`, which raises a KeyError on a miss.
    return KNOWLEDGE_BASE.get(query.strip().lower(), "unknown")


# PYTHON: `dict[str, Callable[[str], str]]` is a type hint meaning "a dict
# whose keys are strings and whose values are functions that take a str
# and return a str". Reading it inside-out: Callable[[str], str] = a
# function; dict[str, that] = a lookup table from name -> function.
# Storing functions as dict values means we can pick which one to call
# *by name at runtime*, which is exactly what the agent loop needs to do.
TOOLS: dict[str, Callable[[str], str]] = {
    "calculator": calculator,
    "knowledge_lookup": knowledge_lookup,
}


# ---------------------------------------------------------------------------
# 2. Agent state: what persists across iterations of the loop.
# ---------------------------------------------------------------------------

# PYTHON: `@dataclass` is a *decorator* — a function that wraps another
# piece of code to add behavior. Putting `@dataclass` above a class
# auto-generates the boilerplate __init__ method from the fields listed
# below, so you get AgentState(goal="...") for free instead of writing
# `def __init__(self, goal, scratchpad, step): self.goal = goal; ...`.
@dataclass
class AgentState:
    goal: str
    # PYTHON: mutable defaults (like an empty list) can't be written as
    # `scratchpad: list[str] = []` directly — every instance would share
    # the *same* list. `field(default_factory=list)` tells dataclass to
    # call list() fresh for each new AgentState instead.
    scratchpad: list[str] = field(default_factory=list)  # tool results so far
    step: int = 0


# ---------------------------------------------------------------------------
# 3. The "brain": decides the next action given the goal and what's been
#    observed so far. Returns either a tool call or a final answer.
# ---------------------------------------------------------------------------

def decide_next_action(state: AgentState) -> dict:
    # PYTHON: `state.goal` reads the `goal` attribute off the AgentState
    # object passed in (dot notation for accessing an object's data).
    goal = state.goal.lower()

    # PYTHON: `"tip" in goal` checks whether the substring "tip" appears
    # anywhere in the string `goal`. `in` works on strings, lists, dicts,
    # and sets — always meaning "is this a member of that collection?".
    if "tip" in goal:
        if state.step == 0:
            # PYTHON: this dict describes an action as data (a "tool_call"
            # with which tool and what input) rather than executing
            # anything directly. Returning data instead of *doing* the
            # thing is what makes this function easy to test and to swap
            # out later for a real LLM call that returns the same shape.
            #
            # "tool_call" here == Claude API's stop_reason "tool_use":
            # the model stopped generating because it wants a tool run
            # before it can keep going, and its response includes a
            # tool_use content block naming the tool + input (what
            # "tool" and "input" below correspond to).
            return {"type": "tool_call", "tool": "knowledge_lookup",
                     "input": "average restaurant tip percent"}
        if state.step == 1:
            return {"type": "tool_call", "tool": "calculator", "input": "84 * 0.15"}
        if state.step == 2:
            # PYTHON: `a, b = some_list` is *tuple/list unpacking* — it
            # assigns the first item to `a` and the second to `b` in one
            # line. Here scratchpad has exactly two entries by this point
            # (the two tool results from steps 0 and 1), in order.
            avg_pct, tip_amount = state.scratchpad
            # PYTHON: `x if condition else y` is a *conditional expression*
            # (a one-line if/else that produces a value). Equivalent to:
            #   if 15 < int(avg_pct): verdict = "below"
            #   else: verdict = "above"
            verdict = "below" if 15 < int(avg_pct) else "above"
            # PYTHON: f"...{expr}..." is an *f-string* — anything inside
            # {curly braces} is evaluated and inserted into the string.
            # A plain string can be split across lines by writing two
            # string literals next to each other; Python concatenates
            # them automatically (no + needed).
            #
            # "final_answer" here == Claude API's stop_reason "end_turn":
            # the model has enough information and is done — it returns
            # plain text with no further tool_use block, so the loop
            # below can stop calling tools and just return this text.
            return {"type": "final_answer",
                     "text": f"A 15% tip on $84 is ${tip_amount}. "
                              f"The average tip is {avg_pct}%, so 15% is {verdict} average."}

    if "capital" in goal:
        if state.step == 0:
            return {"type": "tool_call", "tool": "knowledge_lookup", "input": "capital of france"}
        if state.step == 1:
            # PYTHON: `state.scratchpad[0]` indexes into the list to get
            # its first item (Python indexing starts at 0).
            return {"type": "final_answer", "text": f"The capital of France is {state.scratchpad[0]}."}

    # PYTHON: if none of the `if` branches above returned anything, we
    # fall through to here — the function's fallback case.
    return {"type": "final_answer", "text": "I don't know how to help with that yet."}


# ---------------------------------------------------------------------------
# 4. The agentic loop: plan -> act -> observe -> repeat, with a hard cap
#    on iterations so a broken planner can't loop forever.
# ---------------------------------------------------------------------------

def run_agent(goal: str, max_iterations: int = 6) -> str:
    # PYTHON: `param: type = default` gives a function parameter a
    # default value, so `run_agent("...")` works without specifying
    # max_iterations, but `run_agent("...", max_iterations=10)` can
    # override it.
    state = AgentState(goal=goal)
    print(f"\n=== Goal: {goal} ===")

    # PYTHON: `while condition:` repeats the indented block as long as
    # the condition stays true — this *is* the loop. Each pass through
    # is one round of plan/act/observe.
    while state.step < max_iterations:
        action = decide_next_action(state)  # PLAN

        # This is the branch a real integration checks on stop_reason:
        #   stop_reason == "end_turn"  -> take action["text"], you're done
        #   stop_reason == "tool_use"  -> read the tool_use block(s), run
        #                                 them, and send tool_result back
        if action["type"] == "final_answer":  # end_turn: TERMINATION CONDITION
            print(f"[step {state.step}] final answer -> {action['text']}")
            return action["text"]

        # Falling through here means action["type"] == "tool_call" (tool_use):
        # the model wants a tool executed before it will produce end_turn.
        tool_name = action["tool"]
        tool_input = action["input"]
        # PYTHON: `{value!r}` inside an f-string uses repr() instead of
        # str() — it shows quotes around strings (e.g. '84 * 0.15'
        # instead of 84 * 0.15), which is handy for debug output because
        # it's unambiguous about the value's type.
        print(f"[step {state.step}] thought: need {tool_name}({tool_input!r})")

        # PYTHON: `TOOLS[tool_name]` looks up the function object stored
        # under that key, then `tool_fn(tool_input)` calls it. This is
        # how the agent picks a tool *by name at runtime* instead of
        # writing a separate if/elif for every possible tool.
        tool_fn = TOOLS[tool_name]           # ACT
        result = tool_fn(tool_input)
        print(f"[step {state.step}] observation: {result}")

        # PYTHON: `list.append(x)` adds x to the end of the list in
        # place (it modifies scratchpad directly, no reassignment needed).
        state.scratchpad.append(result)      # OBSERVE: feed result back in
        state.step += 1                      # same as: state.step = state.step + 1

    # PYTHON: `raise` throws an exception, immediately stopping normal
    # execution. This only runs if the while loop exits because
    # max_iterations was hit rather than via the `return` above —
    # i.e. the agent never reached a final answer.
    raise RuntimeError("agent did not converge within max_iterations")


# PYTHON: when you run `python agentic_loop_demo.py` directly, Python sets
# the special variable `__name__` to "__main__" in this file. But if this
# file were *imported* from another file instead, __name__ would be
# "agentic_loop_demo" and this block would be skipped. It's the standard
# way to write "only run this when the file is executed directly, not
# when someone imports functions from it."
if __name__ == "__main__":
    run_agent("How much is a 15% tip on an $84 bill, and is that above or below average?")
    run_agent("What's the capital of France?")
