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
# The MODEL-DRIVEN version: one coordinator session. Subagents are declared
# as AgentDefinitions and handed over via ClaudeAgentOptions.agents; the
# coordinator delegates by calling the built-in "Agent" tool (named "Task"
# in older releases and many exam materials). Routing and delegation policy
# live in the coordinator's system prompt here — Section B moves the same
# policy into Python code so it doesn't depend on the model following it.
#
# Every property below is spelled out with WHY it matters for the exam.
# Real code would leave many of them at their defaults.
#
# from claude_agent_sdk import (
#     AgentDefinition, AssistantMessage, ClaudeAgentOptions, HookMatcher,
#     ResultMessage, SystemMessage, ToolUseBlock, query,
# )
#
# # ------------------------------------------------------------------
# # 1. SUBAGENTS — AgentDefinition(description, prompt, tools, model)
# # ------------------------------------------------------------------
# #   description - the ROUTING signal. The coordinator reads it to decide
# #                 WHEN to delegate to this agent. Say what it does, when to
# #                 use it, and when not to. Vague -> wrong or no delegation.
# #   prompt      - the subagent's own system prompt. It does NOT inherit the
# #                 coordinator's prompt or conversation; per-task facts
# #                 arrive in the brief the coordinator writes.
# #   tools       - the subagent's tool allow-list. RESTRICTS:
# #                   tools=[...] -> only these
# #                   tools=[]    -> no tools at all (pure reasoning)
# #                   omitted     -> INHERITS ALL the coordinator's tools
# #                                  (exam trap: "forgot to scope it")
# #                 Never include "Agent" -> a subagent can't spawn agents
# #                 (hub-and-spoke: only the coordinator delegates).
# #                 MCP tools are listed by full name: mcp__<server>__<tool>.
# #   model       - "sonnet" | "opus" | "haiku" | "inherit" (coordinator's).
# #                 Cheap model for high-volume fan-out, strong model for the
# #                 judgement step.
#
# AGENTS = {
#     "web-search": AgentDefinition(
#         description=(
#             "Finds CURRENT public information on ONE narrow subtopic and "
#             "returns findings with source URLs and publication dates. Use "
#             "for anything time-sensitive or not in the internal corpus. "
#             "Does not analyse documents or draw conclusions."
#         ),
#         prompt=(
#             "You are a web search specialist. You get ONE subtopic; you have "
#             "no other context. Return ONLY JSON: {findings: [{claim, "
#             "source_url, published, confidence}], coverage_gaps: [...]}. "
#             "If a source is unreachable, record it in coverage_gaps - never "
#             "silently drop the subtopic."
#         ),
#         tools=["WebSearch", "WebFetch"],          # web only
#         model="haiku",                            # many parallel calls -> cheap
#     ),
#     "doc-analysis": AgentDefinition(
#         description=(
#             "Extracts findings from the internal research corpus and any "
#             "attached files, with document id and section references. Use "
#             "when the question involves internal or provided documents. "
#             "Does not search the web."
#         ),
#         prompt=(
#             "You are a document analyst. Search the corpus, fetch the "
#             "relevant documents and extract findings with doc_id#section "
#             "references. Same JSON shape as web-search."
#         ),
#         tools=[
#             "Read", "Grep",                       # attached files
#             "mcp__corpus__search_corpus",         # MCP tools, full names
#             "mcp__corpus__fetch_document",
#         ],
#         model="sonnet",
#     ),
#     "synthesis": AgentDefinition(
#         description=(
#             "Combines findings from web-search and doc-analysis into one "
#             "analysis, keeping claim->source attribution, surfacing "
#             "conflicts and listing evidence gaps. Pass it ALL findings "
#             "inline as JSON - it cannot look anything up."
#         ),
#         prompt=(
#             "You receive structured findings ONLY from the coordinator. You "
#             "have no search or file tools. Keep every claim's source. Where "
#             "sources conflict, report both with dates; never silently pick "
#             "one. Return JSON: {narrative, conflicts: [...], gaps: [...]}."
#         ),
#         tools=[],                                 # <-- EXAM TRAP #3, see Section B
#         model="opus",                             # the judgement step
#     ),
#     "report-generation": AgentDefinition(
#         description=(
#             "Formats the final synthesis into a cited Markdown report. Use "
#             "last, after synthesis has no open gaps or refinement is spent."
#         ),
#         prompt=(
#             "You produce the final Markdown report with inline [n] citations "
#             "mapped to sources, plus 'Conflicting evidence' and 'Known "
#             "limitations' sections."
#         ),
#         tools=["Write"],                          # writes report.md, nothing else
#         model="sonnet",
#     ),
# }
#
# # ------------------------------------------------------------------
# # 2. HOOKS — deterministic code around tool calls (not prompt rules)
# # ------------------------------------------------------------------
# async def audit_delegation(input_data, tool_use_id, context):
#     # PreToolUse on the Agent tool sees every delegation BEFORE it runs:
#     # which subagent, and the exact brief it will get. Use it to log
#     # briefs, or deny one that's missing required context.
#     brief = input_data["tool_input"]
#     if brief.get("subagent_type") == "synthesis" and "findings" not in brief.get("prompt", ""):
#         return {"hookSpecificOutput": {
#             "hookEventName": "PreToolUse",
#             "permissionDecision": "deny",
#             "permissionDecisionReason": "Synthesis brief must include the findings JSON.",
#         }}
#     return {}
#
# async def check_subagent_output(input_data, tool_use_id, context):
#     # SubagentStop fires when a subagent finishes: a place to validate its
#     # output (e.g. every finding has a source) before the coordinator uses it.
#     return {}
#
# # ------------------------------------------------------------------
# # 3. COORDINATOR — ClaudeAgentOptions
# # ------------------------------------------------------------------
# options = ClaudeAgentOptions(
#     # --- identity -----------------------------------------------------
#     # A plain string REPLACES Claude Code's default system prompt; the
#     # preset form {"type": "preset", "preset": "claude_code", "append":
#     # "..."} keeps it and adds yours. A pure coordinator wants its own.
#     system_prompt=(
#         "You are a research coordinator. You never research or write "
#         "yourself; you delegate.\n"
#         "1. Decompose the question into subtopics that cover its FULL scope "
#         "(too-narrow decomposition is a failure).\n"
#         "2. Invoke only the subagents the question needs. web-search and "
#         "doc-analysis are independent: invoke them IN PARALLEL (several "
#         "Agent calls in one response).\n"
#         "3. Each subagent sees ONLY your brief: include the subtopic, scope "
#         "and any findings it must work from.\n"
#         "4. Pass ALL findings, as JSON with sources, to synthesis.\n"
#         "5. If synthesis reports gaps, re-delegate narrowly, at most twice; "
#         "then report remaining gaps as known limitations.\n"
#         "6. Finish with report-generation."
#     ),
#     model="sonnet",                       # coordinator's own model
#     fallback_model="haiku",               # used if the primary is unavailable
#
#     # --- the subagents --------------------------------------------------
#     agents=AGENTS,
#
#     # --- tool scoping, level 1: the COORDINATOR -------------------------
#     # allowed_tools = auto-approved without a permission prompt. Only the
#     # delegation tool: the coordinator cannot search or read files itself
#     # and skip its subagents. "Always delegate" becomes architecture, not a
#     # prompt suggestion (EXAM TRAP #3's principle, one level up).
#     allowed_tools=["Agent"],              # "Task" on older SDKs
#     # disallowed_tools = removed from the toolset entirely - a hard block
#     # for tools nobody in this system should touch.
#     disallowed_tools=["Bash", "Edit", "NotebookEdit"],
#     # Tool scoping, level 2, is each AgentDefinition.tools above.
#
#     # --- permissions ----------------------------------------------------
#     # "default"           - anything not pre-approved needs approval
#     # "acceptEdits"       - auto-approve file edits
#     # "plan"              - analyse and propose only, no execution
#     # "bypassPermissions" - approve everything; sandboxed CI only
#     permission_mode="default",
#
#     # --- external tools via MCP -----------------------------------------
#     # Registered once on the coordinator; subagents get access only if
#     # their AgentDefinition.tools lists them (doc-analysis above).
#     mcp_servers={
#         "corpus": {                       # -> mcp__corpus__<tool>
#             "type": "stdio",
#             "command": "python",
#             "args": ["research_corpus_mcp_server.py"],
#         },
#     },
#
#     # --- hooks ----------------------------------------------------------
#     hooks={
#         "PreToolUse": [HookMatcher(matcher="Agent", hooks=[audit_delegation])],
#         "SubagentStop": [HookMatcher(hooks=[check_subagent_output])],
#     },
#
#     # --- project context ------------------------------------------------
#     cwd="./research_workspace",           # where attached docs / report.md live
#     add_dirs=["./attached_docs"],         # extra directories agents may read
#     setting_sources=["project"],          # load .claude/: CLAUDE.md, rules,
#                                           # skills. NOT loaded unless listed.
#
#     # --- guardrails (runaway stops, not the normal way to finish) -------
#     max_turns=30,                         # coordinator turn cap
#     max_budget_usd=5.00,                  # dollar cap: coordinator + ALL subagents
#
#     # --- sessions (not used on a fresh run) -----------------------------
#     # resume="<session_id>"   continue the same research task
#     # fork_session=True       branch it to try a different decomposition
# )
#
# # ------------------------------------------------------------------
# # 4. RUN IT — and read the stream
# # ------------------------------------------------------------------
# async def run(question: str) -> str:
#     async for message in query(prompt=question, options=options):
#         if isinstance(message, SystemMessage) and message.subtype == "init":
#             session_id = message.data.get("session_id")   # save for resume=
#         elif isinstance(message, AssistantMessage):
#             # parent_tool_use_id is None for the coordinator and set for a
#             # subagent's messages -> tells you WHO is acting.
#             for block in message.content:
#                 if isinstance(block, ToolUseBlock) and block.name in ("Agent", "Task"):
#                     # Several Agent blocks in ONE message = parallel fan-out.
#                     print("delegate ->", block.input["subagent_type"])
#         elif isinstance(message, ResultMessage):
#             # subtype "success", or e.g. "error_max_turns" /
#             # "error_max_budget_usd" - a guardrail stopped it: report as
#             # PARTIAL, never as a finished report.
#             print(message.subtype, message.num_turns, message.total_cost_usd)
#             return message.result
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
