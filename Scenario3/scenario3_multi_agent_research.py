"""
CCA-F EXAM PREP — Scenario 3: Multi-Agent Research System
Primary domains: D1 (Agent Architecture), D2 (Context Engineering), D5 (Production/Reliability)

WHAT THIS FILE IS
------------------
A coordinator delegates to 4 specialist subagents (web_search, doc_analysis,
synthesis, report_generation) to produce a cited research report.

This file has two parts:
  SECTION A — a reference snippet (not executed) showing how this maps to the
              real `claude-agent-sdk` (AgentDefinition / ClaudeAgentOptions).
  SECTION B — a runnable simulation of the ORCHESTRATION LOGIC ONLY (no API
              calls, stdlib-only) so you can execute it and watch the
              architecture decisions play out: adaptive routing, parallel
              fan-out, structured provenance, iterative refinement, and
              partial-failure handling.

The three exam traps named in the prompt are each fixed at a specific line —
search for "EXAM TRAP" to jump to them.

Run it with:  python scenario3_multi_agent_research.py
"""

from __future__ import annotations

# ======================================================================
# SECTION A — Reference: real Claude Agent SDK wiring (illustrative only)
# ======================================================================
#
# In the real SDK, subagents are declared as AgentDefinitions and handed to
# the coordinator via ClaudeAgentOptions.agents. The coordinator is just the
# main agent loop, steered by a system prompt that encodes the ROUTING and
# DELEGATION POLICY — the same policy this file implements explicitly in
# Section B so it can be inspected step by step.
#
# from claude_agent_sdk import (
#     ClaudeSDKClient, ClaudeAgentOptions, AgentDefinition, query,
# )
#
# options = ClaudeAgentOptions(
#     # allowed_tools scopes the COORDINATOR's own direct tool access -- this
#     # is a different lever from each AgentDefinition's `tools` below, which
#     # scopes what a SUBAGENT can do once delegated to. Restricting the
#     # coordinator to just "Task" (the delegation tool) means it physically
#     # CANNOT call WebSearch/Read itself and do the work in-line, bypassing
#     # its own subagents. Without this, "always delegate" is just a prompt
#     # suggestion the model could ignore under pressure; with it, it's an
#     # architectural guarantee -- the same principle as EXAM TRAP #3 below,
#     # applied one level up.
#     allowed_tools=["Task"],
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
# SECTION B — Runnable simulation of the orchestration logic
# ======================================================================

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Optional


# ----------------------------------------------------------------------
# Structured context passing with source provenance
# ----------------------------------------------------------------------
# KEY CONCEPT (D2 — Context Engineering): findings flow between agents as
# typed objects carrying provenance, never as a flattened string. This is
# what makes citations in the final report possible, and what lets the
# coordinator judge evidence quality (e.g. "only one low-confidence source
# for this claim") when deciding whether to re-delegate.

@dataclass(frozen=True)
class SourceProvenance:
    agent: str              # which subagent produced this
    origin: str              # URL, file path, etc.
    retrieved_at: str        # ISO timestamp
    confidence: float        # 0.0-1.0, subagent's self-reported confidence


@dataclass(frozen=True)
class Finding:
    claim: str
    provenance: SourceProvenance


@dataclass
class SubagentResult:
    agent_name: str
    success: bool
    findings: list[Finding] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class SynthesisResult:
    narrative: str
    findings_used: list[Finding]
    gaps: list[str] = field(default_factory=list)   # topics needing more evidence


class QueryType(Enum):
    FACTUAL_LOOKUP = auto()      # e.g. "what is the current Fed funds rate"
    DOCUMENT_REVIEW = auto()     # e.g. "summarize the attached 10-K"
    MIXED_RESEARCH = auto()      # needs both web + document evidence


# ----------------------------------------------------------------------
# EXAM TRAP #1 FIX: adaptive routing, not "always call all 4 subagents"
# ----------------------------------------------------------------------
# A coordinator that always fans out to every subagent burns tokens/latency
# on irrelevant calls (e.g. running doc_analysis when no document was
# provided) and increases the surface for partial failures. Classify first,
# route only to what's needed.

def classify_query(query: str, has_attached_docs: bool) -> QueryType:
    """Cheap, fast classification gate — this is intentionally NOT an LLM
    call, because it decides whether to pay for expensive subagent fan-out.
    In production this might be a small/fast model or a rules pass; the
    exam point is that classification happens BEFORE delegation."""
    if has_attached_docs and any(k in query.lower() for k in ("compare", "current", "latest", "market")):
        return QueryType.MIXED_RESEARCH
    if has_attached_docs:
        return QueryType.DOCUMENT_REVIEW
    return QueryType.FACTUAL_LOOKUP


def agents_needed_for(qtype: QueryType) -> set[str]:
    """Routing table. This is the piece a naive design skips — it just
    calls every subagent 'to be safe'. That's the exam trap."""
    return {
        QueryType.FACTUAL_LOOKUP: {"web_search"},
        QueryType.DOCUMENT_REVIEW: {"doc_analysis"},
        QueryType.MIXED_RESEARCH: {"web_search", "doc_analysis"},
    }[qtype]


# ----------------------------------------------------------------------
# Simulated subagents (stand-ins for real SDK subagent invocations)
# ----------------------------------------------------------------------

async def web_search_agent(query: str, *, fail: bool = False, refined: bool = False) -> SubagentResult:
    await asyncio.sleep(0.6)  # simulate network latency
    if fail:
        return SubagentResult("web_search", success=False,
                               error="search API timeout after 3 retries")
    now = datetime.now(timezone.utc).isoformat()
    if refined:
        # A targeted follow-up search (narrower query, built from the gap
        # synthesis reported) turns up a more authoritative source.
        findings = [
            Finding(f"A primary-source dataset independently confirms '{query}'",
                     SourceProvenance("web_search", "https://example-stats.gov/dataset-3", now, 0.93)),
        ]
    else:
        findings = [
            Finding(f"Recent reporting indicates growth trends relevant to '{query}'",
                     SourceProvenance("web_search", "https://example-news.com/article-1", now, 0.82)),
            Finding(f"A secondary source corroborates the '{query}' trend with different figures",
                     SourceProvenance("web_search", "https://example-analysis.com/report-2", now, 0.71)),
        ]
    return SubagentResult("web_search", success=True, findings=findings)


async def doc_analysis_agent(query: str, *, fail: bool = False) -> SubagentResult:
    await asyncio.sleep(0.8)  # simulate parsing latency (slower than search)
    if fail:
        return SubagentResult("doc_analysis", success=False,
                               error="attached document failed to parse (corrupt PDF)")
    now = datetime.now(timezone.utc).isoformat()
    findings = [
        Finding(f"Internal document states a figure directly addressing '{query}'",
                 SourceProvenance("doc_analysis", "uploaded:quarterly_report.pdf#p12", now, 0.95)),
    ]
    return SubagentResult("doc_analysis", success=True, findings=findings)


# ----------------------------------------------------------------------
# EXAM TRAP #3 FIX: synthesis never touches the web — it only reasons over
# findings the coordinator hands it. It CANNOT call web_search_agent or
# doc_analysis_agent itself; there is no such reference in its scope. If it
# judges evidence insufficient, it reports a GAP and hands control back to
# the coordinator, which decides whether/how to re-delegate.
# ----------------------------------------------------------------------

async def synthesis_agent(query: str, findings: list[Finding]) -> SynthesisResult:
    await asyncio.sleep(0.4)
    if not findings:
        return SynthesisResult(narrative="No evidence available.", findings_used=[],
                                gaps=[f"no evidence at all for '{query}'"])

    narrative = "Synthesized view: " + "; ".join(f.claim for f in findings)

    # Simulate the model's own evidence-sufficiency judgment: a claim resting
    # on a single independent source (only one distinct agent, low average
    # confidence) is flagged as a gap rather than silently presented as
    # settled fact. Once a second independent source corroborates it, the
    # gap clears -- this is what lets refinement actually succeed instead of
    # always grinding to the round limit.
    gaps: list[str] = []
    distinct_agents = {f.provenance.agent for f in findings}
    avg_confidence = sum(f.provenance.confidence for f in findings) / len(findings)
    if len(distinct_agents) == 1 and avg_confidence < 0.80:
        weakest = min(findings, key=lambda f: f.provenance.confidence)
        gaps.append(f"single-source, low-confidence claim needs corroboration: '{weakest.claim[:50]}...'")
    if distinct_agents == {"doc_analysis"}:
        gaps.append("no independent external corroboration of internal document claims")

    return SynthesisResult(narrative=narrative, findings_used=findings, gaps=gaps)


async def report_generation_agent(query: str, synthesis: SynthesisResult) -> str:
    await asyncio.sleep(0.3)
    lines = [f"# Research Report: {query}", "", synthesis.narrative, "", "## Sources"]
    for i, f in enumerate(synthesis.findings_used, start=1):
        p = f.provenance
        lines.append(f"[{i}] {f.claim} - *{p.agent}*, {p.origin} "
                      f"(confidence {p.confidence:.2f}, retrieved {p.retrieved_at})")
    if synthesis.gaps:
        lines += ["", "## Known Limitations", *[f"- {g}" for g in synthesis.gaps]]
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Coordinator
# ----------------------------------------------------------------------

MAX_REFINEMENT_ROUNDS = 2   # KEY CONCEPT: refinement must be bounded, or a
                            # perpetually "unsatisfied" synthesis agent loops forever


class Coordinator:
    def __init__(self):
        self.log: list[str] = []

    def _trace(self, msg: str) -> None:
        elapsed = time.monotonic() - self._t0
        self.log.append(f"[t+{elapsed:5.2f}s] {msg}")

    async def run(self, query: str, *, has_attached_docs: bool,
                   simulate_web_failure: bool = False) -> str:
        self._t0 = time.monotonic()
        qtype = classify_query(query, has_attached_docs)
        needed = agents_needed_for(qtype)
        self._trace(f"classified query as {qtype.name} -> routing to {sorted(needed)}")

        all_findings: list[Finding] = []
        failures: list[SubagentResult] = []

        # ------------------------------------------------------------
        # EXAM TRAP #2 area + parallel-vs-sequential:
        # web_search and doc_analysis are INDEPENDENT (neither needs the
        # other's output), so when both are needed they run CONCURRENTLY
        # via asyncio.gather. Only synthesis/report — which genuinely
        # depend on prior output — run sequentially after.
        # ------------------------------------------------------------
        tasks = {}
        if "web_search" in needed:
            tasks["web_search"] = web_search_agent(query, fail=simulate_web_failure)
        if "doc_analysis" in needed:
            tasks["doc_analysis"] = doc_analysis_agent(query)

        if tasks:
            self._trace(f"invoking {list(tasks.keys())} IN PARALLEL (independent subagents)")
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for name, result in zip(tasks.keys(), results):
                # --------------------------------------------------
                # Partial-failure handling (D5): one subagent failing
                # must not abort the whole pipeline. We collect the
                # failure, keep any findings we DO have, and continue —
                # the gap gets surfaced to the user in the final report
                # rather than hidden or fatal.
                # --------------------------------------------------
                if isinstance(result, Exception):
                    self._trace(f"{name} RAISED an exception: {result!r} -- degrading gracefully")
                    failures.append(SubagentResult(name, success=False, error=str(result)))
                elif not result.success:
                    self._trace(f"{name} reported failure: {result.error} -- degrading gracefully")
                    failures.append(result)
                else:
                    self._trace(f"{name} returned {len(result.findings)} finding(s)")
                    all_findings.extend(result.findings)

        if not all_findings and failures:
            self._trace("ALL required subagents failed -- aborting with explicit error")
            return (f"Report generation failed for '{query}': "
                     f"{'; '.join(f.error for f in failures)}")

        # Findings are passed to synthesis as structured Finding objects,
        # not concatenated text -- provenance survives into the report.
        synthesis = await synthesis_agent(query, all_findings)
        self._trace(f"synthesis produced narrative with {len(synthesis.gaps)} gap(s): {synthesis.gaps}")

        # ------------------------------------------------------------
        # Iterative refinement: coordinator reads synthesis.gaps and
        # decides whether/how to re-delegate. This is coordinator policy,
        # not synthesis reaching out on its own (that would be Trap #3).
        # ------------------------------------------------------------
        rounds = 0
        while synthesis.gaps and rounds < MAX_REFINEMENT_ROUNDS:
            rounds += 1
            self._trace(f"refinement round {rounds}: re-delegating to address gaps")
            # Targeted re-delegation: only call the subagent(s) relevant
            # to the gap, not a full re-run of every subagent.
            extra = await web_search_agent(query, refined=True)
            if extra.success:
                all_findings.extend(extra.findings)
                self._trace(f"refinement round {rounds}: web_search added {len(extra.findings)} finding(s)")
            else:
                self._trace(f"refinement round {rounds}: web_search failed again ({extra.error}); stopping refinement")
                break
            synthesis = await synthesis_agent(query, all_findings)
            self._trace(f"refinement round {rounds}: gaps now {synthesis.gaps}")

        if synthesis.gaps:
            self._trace(f"refinement budget exhausted with {len(synthesis.gaps)} gap(s) remaining -- reporting as-is")

        if failures:
            synthesis.gaps.append(
                "partial data: " + "; ".join(f"{f.agent_name} unavailable ({f.error})" for f in failures)
            )

        report = await report_generation_agent(query, synthesis)
        self._trace("report_generation produced final cited report")
        return report


# ----------------------------------------------------------------------
# Demo
# ----------------------------------------------------------------------

async def _demo():
    coordinator = Coordinator()

    print("=" * 70)
    print("QUERY 1: simple factual lookup, no attached docs")
    print("(expect: ONLY web_search invoked -- doc_analysis skipped, Trap #1)")
    print("=" * 70)
    report1 = await coordinator.run(
        "what is the current market growth rate", has_attached_docs=False
    )
    print("\n".join(coordinator.log))
    print("\n--- REPORT ---")
    print(report1)

    coordinator2 = Coordinator()
    print("\n" + "=" * 70)
    print("QUERY 2: mixed research with attached doc; initial web_search fails")
    print("(expect: web_search + doc_analysis IN PARALLEL; the web_search")
    print(" failure degrades gracefully instead of aborting the pipeline;")
    print(" synthesis then flags a corroboration gap and the coordinator")
    print(" re-delegates a targeted retry that resolves it)")
    print("=" * 70)
    report2 = await coordinator2.run(
        "compare current market growth to our latest quarterly figures",
        has_attached_docs=True,
        simulate_web_failure=True,  # deterministic: initial web_search call fails
    )
    print("\n".join(coordinator2.log))
    print("\n--- REPORT ---")
    print(report2)


if __name__ == "__main__":
    asyncio.run(_demo())
