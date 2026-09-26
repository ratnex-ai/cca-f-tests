"""
=============================================================================
 RESEARCH SYSTEM - PART 2 of 2:  AGENTIC LOOP + COORDINATOR + SUBAGENTS
=============================================================================
   pip install anthropic "mcp[cli]"
   python3 corpus_mcp_server.py --build
   export ANTHROPIC_API_KEY=sk-ant-...
   python3 research_system.py

 Every tool call below hits the real corpus built by part 1.
=============================================================================
"""

import json
import os
from dataclasses import dataclass, field

import anthropic

# Import the tool FUNCTIONS from part 1 and call them locally. The `if
# __name__` guard over there stops the MCP server from starting when we do.
from corpus_mcp_server import fetch_document, search_corpus

client = anthropic.Anthropic()      # reads ANTHROPIC_API_KEY from the environment


# =============================================================================
# 1. TOOL DEFINITIONS - the dicts sent in the `tools` request parameter
# =============================================================================
# PYTHON: A LIST OF DICTS. Square brackets hold the list; each `{...}` inside
# is one dict with "key": value pairs. This nesting IS the JSON wire format -
# the SDK serialises it as-is.
TOOLS = [
    {
        "name": "search_corpus",
        "description": (
            "Search the research corpus for documents on a topic. Use this to "
            "discover what evidence exists before reading anything in full. Do "
            "not use it to read a document - call fetch_document for that. "
            "topic must be one of: manufacturing, grid_integration, "
            "regulatory, economics. Returns doc_id, title, publisher, "
            "published, access. A search matching nothing returns found=0, "
            "which is a successful search and must not be retried."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": ["manufacturing", "grid_integration",
                             "regulatory", "economics"],
                },
                "published_after": {
                    "type": "string",
                    "description": "Optional lower bound, YYYY-MM-DD",
                },
            },
            "required": ["topic"],
        },
    },
    {
        "name": "fetch_document",
        "description": (
            "Retrieve the full text of one corpus document. Use after "
            "search_corpus has identified a doc_id worth reading; never guess "
            "a doc_id. Returns title, publisher, published, and body. "
            "Access-restricted documents return errorCategory 'permission' "
            "with isRetryable false - report those as coverage gaps rather "
            "than retrying them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"doc_id": {"type": "string"}},
            "required": ["doc_id"],
        },
    },
]

# PYTHON: A DICT MAPPING NAMES TO FUNCTIONS.
# In Python a function is an ordinary value - you can store it in a dict, pass
# it as an argument, return it. `IMPLEMENTATIONS["search_corpus"]` gives back
# the function itself; adding `(...)` then calls it.
IMPLEMENTATIONS = {
    "search_corpus": search_corpus,
    "fetch_document": fetch_document,
}


def run_tool(name: str, tool_input: dict) -> tuple[str, bool]:
    """Execute one tool and unwrap the MCP result into (json_text, is_error).

    PYTHON: RETURNING TWO VALUES
      `return a, b` builds a tuple. The caller unpacks it with
      `x, y = run_tool(...)`. Python has no out-parameters; this is the idiom.
    """
    fn = IMPLEMENTATIONS[name]
    result = fn(**tool_input)
    # PYTHON: THE ** UNPACKING OPERATOR
    #   fn(**{"doc_id": "DOC-002"})  becomes  fn(doc_id="DOC-002")
    # It splices dict keys into named arguments. This is why the model's
    # `input` dict can be handed straight to your Python function - the keys
    # in input_schema.properties are the parameter names.
    return result.content[0].text, result.is_error


# =============================================================================
# 2. THE AGENTIC LOOP
# =============================================================================
# THE RULE:  loop while stop_reason == "tool_use". Stop on anything else.
#
# Wrong terminators the exam offers as distractors:
#   X  checking whether the last block is a text block
#   X  parsing the text for "done" / "task complete"
#   X  using the iteration cap as the PRIMARY terminator
#
# Also: the API is STATELESS. Nothing is remembered between calls. You resend
# the entire `messages` list every single time, and it only ever grows.
# And: Claude never executes your tool. It emits a REQUEST; your code runs it.

def agentic_loop(system_prompt: str, user_prompt: str,
                 tools: list, max_turns: int = 15) -> str:
    """One agent, running to completion. Returns its final text."""

    # PYTHON: TYPE HINT ON A VARIABLE. `list[dict]` says "a list of dicts".
    # Annotation only; Python does not enforce it.
    messages: list[dict] = [{"role": "user", "content": user_prompt}]

    # PYTHON: range(1, n+1) yields 1, 2, ... n. The upper bound is exclusive,
    # which is why the +1 is there.
    for turn in range(1, max_turns + 1):

        response = client.messages.create(
            model="claude-opus-5",
            max_tokens=4096,
            system=system_prompt,
            tools=tools,
            messages=messages,
            # tool_choice defaults to {"type": "auto"} whenever tools are given.
            #   "auto" -> may call a tool, or may just answer in text
            #   "any"  -> MUST call some tool, model picks which
            #   {"type":"tool","name":"x"} -> MUST call exactly that one
            #   "none" -> no tools this turn
        )

        print(f"[turn {turn}] stop_reason={response.stop_reason}")

        # --- STEP 1: append the assistant turn to history. Always. ---
        messages.append({"role": "assistant", "content": response.content})

        # --- STEP 2: THE ONLY BRANCH THAT MATTERS ---
        if response.stop_reason != "tool_use":
            # end_turn | max_tokens | stop_sequence | refusal | pause_turn
            if response.stop_reason == "max_tokens":
                raise RuntimeError("output truncated - raise max_tokens")
            # PYTHON: A GENERATOR EXPRESSION INSIDE join().
            # Same as a list comprehension but without building the list first.
            # Reads: "the .text of every block whose .type is 'text'".
            return "".join(b.text for b in response.content if b.type == "text")

        # --- STEP 3: run every tool_use block ---
        # Several tool_use blocks in ONE response is PARALLEL tool use. Run
        # them all, then reply once with all the results.
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue        # PYTHON: skip to the next loop iteration
            payload, is_error = run_tool(block.name, block.input)
            print(f"          {block.name}({block.input}) is_error={is_error}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,     # MUST match the tool_use block's id
                "content": payload,
                "is_error": is_error,        # the structured-error signal
            })

        # --- STEP 4: results go back as a USER message ---
        # They must immediately follow the assistant message that requested
        # them. Then the loop repeats and the model reasons with the new facts.
        messages.append({"role": "user", "content": tool_results})

    # max_turns is a RUNAWAY GUARDRAIL, never the primary terminator.
    raise RuntimeError(f"guardrail tripped after {max_turns} turns")


# =============================================================================
# 3. SUBAGENTS
# =============================================================================
# A subagent is not a special object. It is a SEPARATE call with its OWN
# messages list. That separateness IS the context isolation - there is nothing
# to switch on, and nothing is inherited. The prompt string is the only channel.

RESEARCH_AGENT = """You are a research specialist working ONE narrow subtopic.

Search the corpus, then fetch the documents worth reading.

If fetch_document returns errorCategory 'permission', that document is
unavailable to you. Record it as a coverage_gap using the partialResults
metadata you were given. Do not retry it.
If search_corpus returns found=0, that is a complete answer meaning no such
evidence exists in the corpus. Do not retry it.

Return ONLY a JSON array. No prose, no reasoning chains - the downstream agent
has a limited context budget."""

SYNTHESIS_AGENT = """You are a synthesis specialist.

You receive findings inline. You have NO memory of the agents that produced
them, and no tools - you cannot look anything up.

- Preserve every claim -> source -> published_date mapping into the report.
- Where two credible sources conflict, present BOTH with attribution and
  dates. Never silently pick one.
- Open with a Coverage section naming every coverage_gap in the findings.
- Never fill a gap from your own knowledge; you cannot attribute it."""


@dataclass
class Delegation:
    """The payload the coordinator hands a subagent.

    PYTHON: @dataclass
      A decorator that writes __init__ for you. Without it you would hand-write
      `def __init__(self, scope, goal, ...)` and assign every field. With it,
      `Delegation(scope="x", goal="y")` just works.

    PYTHON: field(default_factory=list)
      Never write `out_of_scope: list = []`. That single list object would be
      SHARED by every instance - append to one and it appears in all of them.
      default_factory calls list() fresh for each instance.
    """
    scope: str
    goal: str
    output_contract: str
    out_of_scope: list = field(default_factory=list)
    findings: dict = field(default_factory=dict)

    def render(self) -> str:
        """Assemble the string that becomes the subagent's entire world.

        PYTHON: `self`
          Inside a class, `self` is the instance. `self.scope` reads this
          particular Delegation's scope. Methods always take it as arg 1;
          you never pass it explicitly at the call site.
        """
        parts = [f"## YOUR TASK\n{self.scope}",
                 f"\n## GOAL\n{self.goal}"]

        if self.out_of_scope:
            # PYTHON: `+=` on a list extends it with another list's items.
            parts += ["\n## OUT OF SCOPE - other agents cover these"]
            parts += [f"- {t}" for t in self.out_of_scope]

        if self.findings:
            parts += [
                "\n## FINDINGS FROM PRIOR AGENTS",
                "This is your ENTIRE context. You cannot query the agents "
                "that produced it.",
                json.dumps(self.findings, indent=2),
            ]

        parts += [f"\n## OUTPUT CONTRACT\n{self.output_contract}"]
        return "\n".join(parts)     # glue the list into one string with newlines


# =============================================================================
# 4. THE COORDINATOR
# =============================================================================
# Four responsibilities: DECOMPOSE, DELEGATE, AGGREGATE, EVALUATE.
# Three of the four are judgment calls. A coordinator that runs the identical
# sequence on every input is a fixed pipeline wearing a coordinator's name.

# Which corpus topics a question actually needs. Mapping table for the demo;
# in production you would ask the model to decompose.
TOPIC_KEYWORDS = {
    "manufacturing": ["manufactur", "capacity", "supply", "production"],
    "grid_integration": ["grid", "integration", "inverter", "interconnect"],
    "regulatory": ["regulat", "policy", "permit", "compliance"],
    "economics": ["cost", "price", "econom", "$"],
}


def decompose(question: str) -> list[str]:
    """Pick the topics this question needs. DYNAMIC ROUTING.

    A narrow question gets one subagent. A broad one gets all four. The Q3
    failure was a coordinator that always ran the full pipeline; the Q4
    failure was one whose subtopics all landed in a single bucket.
    """
    q = question.lower()        # PYTHON: strings have methods; .lower() folds case
    hits = [topic for topic, words in TOPIC_KEYWORDS.items()
            if any(w in q for w in words)]
    # PYTHON: .items() yields (key, value) pairs, unpacked into topic, words.
    # PYTHON: any(...) is True if at least one item is True. all(...) needs all.
    # PYTHON: `"grid" in q` does substring matching on strings.

    # A broad question matches nothing specific -> cover everything, so we do
    # not repeat Q4's narrow-decomposition failure.
    return hits if hits else list(TOPIC_KEYWORDS)
    # PYTHON: `a if cond else b` is the ternary - one-line if/else.
    # PYTHON: list(some_dict) gives its KEYS as a list.


def coordinator(question: str) -> str:
    print(f"\ncoordinator: {question!r}")

    # --- DECOMPOSE ---
    topics = decompose(question)
    print(f"coordinator: routing to {len(topics)} subagent(s): {topics}")

    # --- DELEGATE ---
    findings = {}
    for topic in topics:
        task = Delegation(
            scope=f"Gather corpus evidence on the topic '{topic}'.",
            goal=f"Establish what the corpus says about {topic} relevant to: "
                 f"{question}",
            out_of_scope=[t for t in topics if t != topic],
            output_contract=(
                'JSON array. Each element: {"claim","source_doc_id",'
                '"publisher","published_date","excerpt"} or, when a document '
                'was unreachable, {"coverage_gap","doc_id","attempted"}.'
            ),
        )
        # Each call = a fresh context window. Nothing carries over.
        findings[topic] = agentic_loop(RESEARCH_AGENT, task.render(), TOOLS)

    # --- AGGREGATE ---
    # The Q1 fix: findings are passed INLINE. The Q2 fix: the structured
    # claim -> source -> date mappings pass through unflattened.
    synthesis_task = Delegation(
        scope=f"Write the final analyst report answering: {question}",
        goal="Every factual claim must trace to a named source document.",
        findings=findings,
        output_contract="Markdown report. Coverage section first.",
    )

    # No tools here. A synthesis agent given search tools starts doing its own
    # research mid-report and contradicts the agents upstream of it.
    return agentic_loop(SYNTHESIS_AGENT, synthesis_task.render(), tools=[])


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("set ANTHROPIC_API_KEY, and run "
                         "`python3 corpus_mcp_server.py --build` first")

    # NARROW question -> one subagent. Compare the routing line it prints.
    print(coordinator("What does the corpus say about grid integration?"))

    # BROAD question -> all four subagents.
    print(coordinator("What's the current state of grid-scale sodium-ion "
                      "storage?"))
