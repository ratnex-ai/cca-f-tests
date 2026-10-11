"""
CCA-F EXAM PREP — Scenario 3: Multi-Agent Research System
Primary domains: D1 (Agent Architecture), D2 (Context Engineering), D5 (Production/Reliability)

WHAT THIS FILE IS
------------------
A coordinator delegates to 4 specialist subagents (web_search, doc_analysis,
synthesis, report_generation) to produce a cited research report.

This file has two parts:
  SECTION A — a reference snippet (not executed): the MODEL-DRIVEN version,
              one coordinator session with agents={...AgentDefinition...}
              delegating through the built-in Agent tool.
  SECTION B — a DIRECTIONAL SKETCH of the code-driven version (real class
              names, simplified plumbing, not meant to be run): how the
              coordinator calls each subagent via query() +
              ClaudeAgentOptions, and exactly what data is handed over at
              each step.

The three exam traps are each fixed at a specific line — search for
"EXAM TRAP" to jump to them:
  #1 always fanning out to every subagent      -> classify + route first
  #2 running independent subagents one by one  -> asyncio.gather
  #3 letting synthesis search the web itself   -> synthesis has zero tools
"""

from __future__ import annotations

# ======================================================================
# SECTION A — Reference: real Claude Agent SDK wiring (illustrative only)
# ======================================================================
#
# In the real SDK, subagents are declared as AgentDefinitions and handed to
# the coordinator via ClaudeAgentOptions.agents. The coordinator is just the
# main agent loop, steered by a system prompt that encodes the ROUTING and
# DELEGATION POLICY — the same policy Section B implements in Python code,
# so it can be inspected step by step and doesn't depend on the model
# following the prompt. (The delegation tool is named "Agent" in current
# SDK releases; older releases and docs call it "Task".)
#
# from claude_agent_sdk import (
#     ClaudeSDKClient, ClaudeAgentOptions, AgentDefinition, query,
# )
#
# options = ClaudeAgentOptions(
#     # allowed_tools scopes the COORDINATOR's own direct tool access -- this
#     # is a different lever from each AgentDefinition's `tools` below, which
#     # scopes what a SUBAGENT can do once delegated to. Restricting the
#     # coordinator to just "Agent" (the delegation tool) means it physically
#     # CANNOT call WebSearch/Read itself and do the work in-line, bypassing
#     # its own subagents. Without this, "always delegate" is just a prompt
#     # suggestion the model could ignore under pressure; with it, it's an
#     # architectural guarantee -- the same principle as EXAM TRAP #3 below,
#     # applied one level up.
#     allowed_tools=["Agent"],                   # "Task" on older SDKs
#     agents={
#         "web-search": AgentDefinition(
#             description="Searches the web for current information",
#             prompt="You search the web and return findings with source "
#                    "URLs. You never synthesize or draw conclusions.",
#             tools=["WebSearch", "WebFetch"],   # <-- scoped tool access
#             model="sonnet",
#         ),
#         "doc-analysis": AgentDefinition(
#             description="Extracts findings from provided documents/files",
#             prompt="You analyze the documents you are given and return "
#                    "findings with page/section references. You do not "
#                    "search the web.",
#             tools=["Read", "Grep"],
#             model="sonnet",
#         ),
#         "synthesis": AgentDefinition(
#             description="Combines findings from other subagents into a "
#                          "coherent narrative and flags evidence gaps",
#             prompt="You receive structured findings ONLY from the "
#                    "coordinator. You have no search or file tools — you "
#                    "reason over what you're given and report gaps.",
#             tools=[],                          # <-- EXAM TRAP #3, see below
#             model="opus",
#         ),
#         "report-generation": AgentDefinition(
#             description="Formats final findings + synthesis into a cited "
#                          "report",
#             prompt="You produce the final Markdown report with inline "
#                    "citations mapped to source provenance.",
#             tools=[],
#             model="sonnet",
#         ),
#     },
#     system_prompt=(
#         "You are a research coordinator. Classify each query first; only "
#         "invoke the subagents the query actually needs. web-search and "
#         "doc-analysis are independent — invoke them concurrently when "
#         "both are needed. Always pass structured findings (with source "
#         "provenance), never raw text, to synthesis and report-generation. "
#         "After synthesis, check its reported gaps; if any remain and you "
#         "have refinement budget left, re-delegate narrowly to fill them."
#     ),
# )
#
# The key point for the exam: tool scoping happens at TWO levels here --
# `allowed_tools` on ClaudeAgentOptions restricts the coordinator to only
# delegating, and each AgentDefinition's `tools` (e.g. synthesis's empty
# list) restricts what that subagent can do once invoked. Together they
# enforce "synthesis must not query the web directly" architecturally,
# rather than relying on the prompt asking nicely.

# ======================================================================
# SECTION B — Directional sketch: coordinator -> subagents -> data flow
# ======================================================================
#
# Not meant to be run as-is. Read it top to bottom: it shows WHO calls
# WHOM and WHAT DATA is handed over at each step, which is what the exam
# asks about. Real class names, simplified plumbing.
#
#   question
#      │
#      ▼
#   COORDINATOR (Python) ── 1. classify + route ──────── EXAM TRAP #1
#      │
#      ├── brief ──► web_search   ─┐  2. independent → run in PARALLEL
#      ├── brief ──► doc_analysis ─┤                 ── EXAM TRAP #2
#      │                           │
#      │◄── JSON findings + sources┘  3. parse into typed Finding objects
#      │
#      ├── ALL findings inline ──► synthesis (NO tools) ─ EXAM TRAP #3
#      │◄── narrative + gaps
#      │
#      ├── gaps? ── targeted brief ──► web_search   4. bounded refinement
#      │
#      ├── synthesis + sources ──► report_generation
#      ▼
#   cited report (+ Known Limitations for gaps / failed subagents)

import asyncio
import json
from dataclasses import asdict, dataclass

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

MAX_REFINEMENT_ROUNDS = 2


# ----------------------------------------------------------------------
# 1. THE DATA CONTRACT between agents
# ----------------------------------------------------------------------
# Subagents hand back structured findings WITH their source - never a
# prose blob. That's what keeps every claim citable all the way into the
# final report.

@dataclass
class Finding:
    claim: str
    source: str          # URL, or file#section for documents
    agent: str           # which subagent produced it
    confidence: float    # 0.0-1.0


FINDINGS_FORMAT = (
    'Return ONLY JSON: {"findings": [{"claim": "...", "source": "...", '
    '"confidence": 0.0}], "coverage_gaps": ["..."]}'
)


# ----------------------------------------------------------------------
# 2. THE SUBAGENTS - one ClaudeAgentOptions each
# ----------------------------------------------------------------------
# Each subagent is a separate query() session: its own system prompt, its
# own tools, its own (empty) context. It knows NOTHING except the brief the
# coordinator sends it.

SUBAGENTS = {
    "web_search": ClaudeAgentOptions(
        system_prompt="You search the web and report findings with URLs. "
                      "You never draw overall conclusions. " + FINDINGS_FORMAT,
        allowed_tools=["WebSearch", "WebFetch"],     # web only
        model="sonnet",
        max_turns=8,
    ),
    "doc_analysis": ClaudeAgentOptions(
        system_prompt="You read the attached documents and report findings "
                      "with file#section references. " + FINDINGS_FORMAT,
        allowed_tools=["Read", "Grep", "Glob"],      # files only, no web
        cwd="./attached_docs",
        model="sonnet",
        max_turns=8,
    ),
    "synthesis": ClaudeAgentOptions(
        system_prompt="You combine the findings you are given. Cite them as "
                      "[n]. Report conflicts and evidence gaps. Return ONLY "
                      'JSON: {"narrative": "...", "gaps": ["..."]}',
        # EXAM TRAP #3 FIX: synthesis gets NO tools - it cannot search to
        # fill a gap itself. It reports the gap; the coordinator decides.
        allowed_tools=[],
        disallowed_tools=["WebSearch", "WebFetch", "Read", "Agent"],
        model="opus",                                # the judgement step
        max_turns=1,
    ),
    "report_generation": ClaudeAgentOptions(
        system_prompt="You format a synthesis into a Markdown report. Keep "
                      "every [n] citation.",
        allowed_tools=[],
        model="sonnet",
        max_turns=1,
    ),
}


async def call_subagent(name: str, brief: str) -> str:
    """Coordinator -> subagent. The brief is the subagent's ENTIRE context,
    so it must be self-contained."""
    async for message in query(prompt=brief, options=SUBAGENTS[name]):
        if isinstance(message, ResultMessage):
            return message.result        # the subagent's final answer
    raise RuntimeError(f"{name} returned no result")


def to_findings(agent: str, raw_json: str) -> list[Finding]:
    """Subagent output -> typed Findings, tagged with who produced them."""
    return [Finding(agent=agent, **f) for f in json.loads(raw_json)["findings"]]


# ----------------------------------------------------------------------
# 3. THE COORDINATOR - every hand-off between agents passes through here
# ----------------------------------------------------------------------
# Hub-and-spoke: subagents never call each other. The coordinator decides
# who runs, builds each brief, and carries the data from one to the next.

async def coordinator(question: str, has_attached_docs: bool) -> str:

    # --- Step 1: classify, then route ---------------------------------
    # EXAM TRAP #1 FIX: only call the subagents this question needs - not
    # "all of them to be safe". No documents -> no doc_analysis.
    needed = ["web_search"]
    if has_attached_docs:
        needed.append("doc_analysis")

    # --- Step 2: delegate - in parallel -------------------------------
    briefs = {
        "web_search": f"Research question: {question}\n"
                      "Find current public sources.",
        "doc_analysis": f"Research question: {question}\n"
                        "Extract what the attached documents say about it.",
    }
    # EXAM TRAP #2 FIX: web_search and doc_analysis don't need each other's
    # output, so they run at the same time. return_exceptions=True: one
    # failing subagent doesn't cancel the other.
    outputs = await asyncio.gather(
        *(call_subagent(name, briefs[name]) for name in needed),
        return_exceptions=True,
    )

    # --- Step 3: collect structured findings; keep going on failure ---
    findings: list[Finding] = []
    failures: list[str] = []
    for name, output in zip(needed, outputs):
        if isinstance(output, Exception):
            failures.append(f"{name} unavailable: {output}")   # reported, not hidden
        else:
            findings += to_findings(name, output)

    # --- Step 4: synthesis - ALL findings passed INLINE ---------------
    # Synthesis has no memory of the other subagents and can't look
    # anything up, so the findings (with sources) go IN the brief.
    synthesis = json.loads(await call_subagent(
        "synthesis",
        f"Question: {question}\n\nFINDINGS (your entire context):\n"
        + json.dumps([asdict(f) for f in findings], indent=2),
    ))

    # --- Step 5: bounded refinement - the COORDINATOR fills gaps ------
    for _ in range(MAX_REFINEMENT_ROUNDS):       # never loop forever
        if not synthesis["gaps"]:
            break
        extra = await call_subagent(             # narrow brief, not a full re-run
            "web_search",
            f"Targeted follow-up. Find primary sources for: {synthesis['gaps']}",
        )
        findings += to_findings("web_search", extra)
        synthesis = json.loads(await call_subagent(
            "synthesis",
            f"Question: {question}\n\nFINDINGS:\n"
            + json.dumps([asdict(f) for f in findings], indent=2),
        ))

    # --- Step 6: report - sources and limitations travel with it ------
    report = await call_subagent(
        "report_generation",
        f"Question: {question}\n\nSynthesis:\n{synthesis['narrative']}",
    )
    sources = "\n".join(f"[{i}] {f.claim} - {f.source} ({f.agent})"
                        for i, f in enumerate(findings, start=1))
    limitations = synthesis["gaps"] + failures   # leftover gaps + failed agents
    return (f"{report}\n\n## Sources\n{sources}"
            + ("\n\n## Known Limitations\n" + "\n".join(f"- {x}" for x in limitations)
               if limitations else ""))


# Usage:
#   asyncio.run(coordinator(
#       "Compare current grid-scale battery market growth to our Q2 figures",
#       has_attached_docs=True,
#   ))
