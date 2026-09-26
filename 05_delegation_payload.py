"""
05 - WHAT A COORDINATOR MUST PASS TO A SUBAGENT  (CCA-F Domain 1.3)
=============================================================================
The prompt string is the ONLY channel from parent to child. This file builds
that string properly, then shows the two real spawn mechanisms.

    pip install anthropic claude-agent-sdk
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 05_delegation_payload.py
=============================================================================
"""

import json
import os
from dataclasses import dataclass, field

import anthropic

client = anthropic.Anthropic()


# =============================================================================
# THE SEVEN THINGS. Modelled as a dataclass so nothing can be forgotten -
# Python will raise TypeError if a required field is missing at construction.
# =============================================================================
@dataclass
class Delegation:
    # 1. SCOPE - one narrow task. Not "research batteries".
    scope: str

    # 2. GOAL + QUALITY CRITERIA - what "done well" means. Deliberately NOT a
    #    procedure. "Find 5+ primary sources published after 2025-01" lets the
    #    subagent adapt; "first search X, then search Y" does not.
    goal: str
    quality_criteria: list[str]

    # 3. UPSTREAM FINDINGS - inline, complete. The subagent has NO access to
    #    the coordinator's history. If it is not here, it does not exist.
    upstream_findings: dict = field(default_factory=dict)

    # 4. BOUNDARIES - what other agents are covering, so this one does not
    #    duplicate retrieval or wander outside its partition.
    out_of_scope: list[str] = field(default_factory=list)

    # 5. OUTPUT CONTRACT - the exact structure expected back.
    output_schema: dict | None = None

    # 6. METADATA REQUIREMENTS - the fields that must survive to the report.
    required_metadata: list[str] = field(
        default_factory=lambda: ["source_url", "published_date", "excerpt"]
    )
    # PYTHON NOTE: default_factory needs a zero-argument callable. `lambda:`
    # wraps the list literal into one. Writing `default_factory=[...]` fails.

    # 7. FAILURE PROTOCOL - how to report being blocked, so the coordinator can
    #    distinguish "source unreachable" from "searched, found nothing".
    failure_protocol: str = (
        "If a source is unreachable, emit a coverage_gap entry naming the "
        "subtopic, what you attempted, and any partial results. Do NOT return "
        "an empty array - the coordinator cannot distinguish that from a "
        "successful search with no matches."
    )

    def render(self) -> str:
        """Assemble the single string that becomes the subagent's whole world.

        Note the ORDERING. Key material goes first and last; bulk findings sit
        in the middle. That is the lost-in-the-middle mitigation from Domain
        5.1 applied to a delegation prompt.
        """
        parts = [
            "## YOUR TASK",
            self.scope,
            "",
            "## GOAL",
            self.goal,
            "",
            "## QUALITY CRITERIA",
            *[f"- {c}" for c in self.quality_criteria],
            # PYTHON NOTE: * unpacks the list comprehension's items as separate
            # elements of `parts`, instead of nesting a list inside a list.
        ]

        if self.out_of_scope:
            parts += [
                "",
                "## OUT OF SCOPE - other agents are covering these, do not "
                "duplicate their retrieval",
                *[f"- {t}" for t in self.out_of_scope],
            ]

        if self.upstream_findings:
            parts += [
                "",
                "## FINDINGS FROM PRIOR AGENTS",
                "This is your ENTIRE context. You have no memory of the agents "
                "that produced it and cannot query them.",
                "```json",
                json.dumps(self.upstream_findings, indent=2),
                "```",
            ]

        parts += [
            "",
            "## REQUIRED METADATA",
            "Every claim you emit must carry: " + ", ".join(self.required_metadata),
            "Attribution is lost permanently if you drop it here - downstream "
            "agents cannot recover it.",
            "",
            "## IF YOU CANNOT COMPLETE",
            self.failure_protocol,
        ]

        if self.output_schema:
            parts += [
                "",
                "## OUTPUT CONTRACT",
                "Return ONLY JSON matching this schema. No prose, no reasoning "
                "chains - the downstream agent has a limited context budget.",
                "```json",
                json.dumps(self.output_schema, indent=2),
                "```",
            ]

        return "\n".join(parts)


# =============================================================================
# CONCRETE EXAMPLE 1 - coordinator delegating to a SEARCH subagent.
# Nothing upstream yet, so upstream_findings is empty. Boundaries matter most.
# =============================================================================
SEARCH_OUTPUT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "claim": {"type": "string"},
            "source_url": {"type": "string"},
            "published_date": {"type": "string", "description": "YYYY-MM-DD"},
            "excerpt": {"type": "string"},
            "relevance": {"type": "number"},
            "coverage_gap": {"type": ["string", "null"]},
        },
        "required": ["claim", "source_url", "published_date"],
    },
}

search_task = Delegation(
    scope="Grid integration challenges for grid-scale sodium-ion storage: "
          "inverter compatibility, frequency response, and interconnection "
          "queue constraints.",
    goal="Establish what the current evidence says about whether sodium-ion "
         "chemistry poses integration problems distinct from lithium-ion.",
    quality_criteria=[
        "At least 5 primary sources: utility filings, grid operator reports, "
        "peer-reviewed papers. Not trade-press summaries of those sources.",
        "Published 2025-01-01 or later; note explicitly if the best available "
        "evidence predates that.",
        "Where sources disagree on a figure, return BOTH with attribution "
        "rather than selecting one.",
    ],
    out_of_scope=[
        "Manufacturing capacity and supply chain (agent search-1)",
        "Cell-level cost curves (agent search-2)",
        "Regulatory and permitting barriers (agent search-4)",
    ],
    output_schema=SEARCH_OUTPUT_SCHEMA,
)


# =============================================================================
# CONCRETE EXAMPLE 2 - coordinator delegating to SYNTHESIS.
# Here upstream_findings is the whole point. This is the Q1 fix.
# =============================================================================
def build_synthesis_task(question: str, findings: dict) -> Delegation:
    return Delegation(
        scope=f"Write the final analyst report answering: {question}",
        goal="Produce a report a client can cite, in which every factual "
             "claim traces to a named source.",
        quality_criteria=[
            "Preserve every claim -> source_url -> published_date mapping "
            "from the findings below, unchanged, into the report.",
            "Where two credible sources conflict, present BOTH values with "
            "attribution and dates. Do not silently reconcile them - a 2024 "
            "figure differing from a 2026 figure is a trend, not a conflict.",
            "Open with a 'Coverage' section naming any subtopic in the "
            "findings that carries a coverage_gap or has no supporting "
            "sources. Do not omit thin areas silently.",
            "Render financial and capacity data as tables; render narrative "
            "findings as prose. Do not flatten everything into bullets.",
        ],
        upstream_findings=findings,
        # No out_of_scope: synthesis covers everything by definition.
        required_metadata=["source_url", "published_date"],
        failure_protocol=(
            "If the findings do not support a claim the question demands, say "
            "so explicitly in the Coverage section. Never fill the gap from "
            "your own knowledge - you have no way to attribute it."
        ),
    )


# =============================================================================
# SPAWNING - two real mechanisms.
# =============================================================================

# --- Mechanism 1: raw Messages API. A subagent IS a separate create() call
#     with its own messages list. That separate list is the isolation.
def spawn_via_api(system_prompt: str, task: Delegation,
                  tools: list | None = None) -> str:
    kwargs = {
        "model": "claude-opus-5",
        "max_tokens": 8192,
        "system": system_prompt,
        "messages": [{"role": "user", "content": task.render()}],
    }
    if tools:
        kwargs["tools"] = tools
    response = client.messages.create(**kwargs)
    return "".join(b.text for b in response.content if b.type == "text")


# --- Mechanism 2: Claude Agent SDK. The coordinator emits a tool call whose
#     `prompt` input carries exactly the same rendered string.
#
#     Naming, and this is an exam trap: the spawn tool was renamed
#     Task -> Agent in Claude Code v2.1.63. Exam Guide v1.0 still says "Task"
#     and still says allowedTools must include it. Answer Task on the exam;
#     list both in real code.
#
# from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition
#
# options = ClaudeAgentOptions(
#     allowed_tools=["WebSearch", "Read", "Agent", "Task"],
#     agents={
#         "searcher": AgentDefinition(
#             description="Finds primary sources for ONE narrow subtopic.",
#             prompt=SEARCH_SYSTEM_PROMPT,   # the agent's PERSISTENT identity
#             tools=["WebSearch", "Read"],
#         ),
#     },
# )
#
# The split matters:
#   AgentDefinition.prompt  = who this agent always is       (static)
#   Agent tool's `prompt`   = what to do THIS time, + context (per-invocation)
# task.render() produces the second. Do not put findings in the first.
#
# PARALLEL SPAWNING: emit MULTIPLE Agent tool calls in ONE assistant response.
# Spreading them across separate turns runs them sequentially instead.


# =============================================================================
# WHAT NOT TO PASS
# =============================================================================
def antipattern_dump_history(messages: list) -> str:
    """DO NOT DO THIS.

    Pasting the coordinator's transcript in defeats the reason you spawned a
    subagent. You wanted a fresh context window holding only relevant material;
    this refills it with the parent's tool results and reasoning chains, and
    reintroduces the lost-in-the-middle problem you were escaping.

    Pass distilled FINDINGS, not the conversation that produced them.
    """
    return json.dumps(messages)


if __name__ == "__main__":
    print("=" * 70)
    print("SEARCH DELEGATION")
    print("=" * 70)
    print(search_task.render())

    print("\n" + "=" * 70)
    print("SYNTHESIS DELEGATION (abridged findings)")
    print("=" * 70)
    demo_findings = {
        "grid_integration": [{
            "claim": "Sodium-ion units required no inverter modification in "
                     "the Datang 50MWh pilot.",
            "source_url": "https://example-grid-operator.cn/report-2026-03",
            "published_date": "2026-03-14",
            "excerpt": "...existing PCS hardware was retained...",
            "relevance": 0.9,
        }],
        "regulatory": [{
            "coverage_gap": "Regulatory barriers: EU filings were paywalled; "
                            "retrieved 1 of 6 target documents.",
            "claim": None, "source_url": None, "published_date": None,
        }],
    }
    task = build_synthesis_task(
        "What's the current state of grid-scale sodium-ion storage?",
        demo_findings,
    )
    print(task.render())

    if os.environ.get("ANTHROPIC_API_KEY"):
        print("\n--- live run ---")
        print(spawn_via_api("You are a synthesis specialist.", task))