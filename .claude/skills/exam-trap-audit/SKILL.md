---
name: exam-trap-audit
description: Use when checking whether a CCA-F ScenarioN file in this repo actually follows the repo's rubric — correct header/domain tags, a real SECTION A / SECTION B split, and every named EXAM TRAP genuinely fixed in code (not just described in a comment). Trigger on "audit scenario", "check this scenario against the rubric", "did I actually fix the exam trap", or before treating a new scenario file as done.
---

# Exam trap audit

A scenario file can *look* right — right headers, right section names — while
quietly failing the one thing that matters: does SECTION B's code actually
avoid the trap, or does it just have a comment claiming it does? This skill
checks the code, not the prose.

## Procedure

1. **Read the target file in full.** If not given a path, ask which
   `ScenarioN/scenarioN_*.py` file to audit, or infer it from the most
   recently edited scenario file.

2. **Header check.**
   - First line of the docstring matches `CCA-F EXAM PREP — Scenario N: <Title>`.
   - A `Primary domains: D#, ...` line is present and those domains are real
     (cross-check against the table in `CLAUDE.md` — flag anything not listed
     there as unverified rather than assuming it's correct).

3. **Structure check.**
   - There is a `SECTION A` block that is entirely commented out / non-executing
     reference code (real SDK class names like `AgentDefinition`,
     `ClaudeAgentOptions`, `tool_choice` are expected here — that's correct,
     not a bug).
   - There is a `SECTION B` block that is runnable and imports **no**
     `anthropic` / `claude_agent_sdk` and makes **no** network calls
     (`requests`, `httpx`, `urllib`, raw sockets). If SECTION B imports any of
     these, that's a rubric violation — it's supposed to be a stdlib-only
     orchestration simulation. Flag it.

4. **Per-trap check — the part that actually matters.** For each trap named in
   the header:
   - Find the `# EXAM TRAP #k FIX:` comment that claims to fix it.
   - Read the surrounding code and independently judge: does this code
     structurally prevent the trap, or does it just assert in a comment that
     it does? Example of a real fix: a routing function that only invokes the
     subagents a classifier selected (Scenario3's `agents_needed_for`).
     Example of a fake fix: a comment saying "we don't fan out to everything"
     directly above a call that still invokes every subagent.
   - If a trap is named in the header but has no corresponding
     `EXAM TRAP #k FIX` comment anywhere in the file, that's a finding on its
     own — the trap was never addressed.

5. **Run it.** Execute `python3 <path>` and confirm:
   - It exits 0 with no traceback.
   - Its own printed output plausibly demonstrates each trap's fix (e.g. a
     "doc_analysis skipped" line for an adaptive-routing trap). If the demo
     doesn't actually exercise a named trap, note that as a coverage gap.

6. **Report** as a short pass/fail list, one line per rubric item from steps
   2–5, each ending in ✅ or ❌ plus a one-sentence reason. Do not silently fix
   issues you find — report them and ask before editing the scenario file,
   since trap-fixing is the exam-relevant part the user is meant to have done
   themselves.

## Example

Auditing `Scenario3/scenario3_multi_agent_research.py` against its own header
(traps #1 adaptive routing, #3 synthesis-can't-touch-the-web) should conclude:
trap #1 ✅ (`agents_needed_for` routes per `QueryType`, `web_search_agent` /
`doc_analysis_agent` are only invoked when in `needed`), trap #3 ✅
(`synthesis_agent` takes only a `findings: list[Finding]` argument — there is
no code path from it to either agent function). That's the bar every other
scenario file should be held to.
