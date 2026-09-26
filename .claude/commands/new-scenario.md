---
description: Scaffold a new CCA-F ScenarioN file following this repo's SECTION A / SECTION B / EXAM TRAP convention
argument-hint: <number> <slug> <"Title"> <D#,D#,...> <"trap 1" | "trap 2" | ...>
---

Scaffold a new scenario file from the arguments: `$ARGUMENTS`

Parse them as: `<number> <slug> <"Title"> <domains, e.g. D1,D5> <traps, pipe-separated>`

Example invocation:
`/new-scenario 7 tool_error_handling "Tool Error Recovery" D2,D5 "retries with the exact same input on a transient failure | swallows the tool error instead of surfacing it to the model"`

Steps:

1. Confirm `ScenarioN/` doesn't already exist. If the number is already taken,
   stop and ask instead of overwriting.
2. Create `ScenarioN/scenarioN_<slug>.py` matching the structure of
   `Scenario3/scenario3_multi_agent_research.py` exactly:
   - Header docstring: `CCA-F EXAM PREP — Scenario N: <Title>`, a
     `Primary domains: <D#, D#>` line, a short "WHAT THIS FILE IS" paragraph,
     and a note that each named trap is fixed at a line marked `EXAM TRAP #k`.
   - `SECTION A` — a commented-out reference block showing the real
     `claude-agent-sdk` / Anthropic Messages API wiring this scenario maps to.
     Never executed; illustrative only.
   - `SECTION B` — a runnable, **stdlib-only** simulation (asyncio/dataclasses/
     enum are fine; no `anthropic` import, no network I/O) of just the
     orchestration logic, with one `# EXAM TRAP #k FIX:` comment block per
     trap passed in, placed at the exact line that fixes it.
   - A `_demo()` async function plus `if __name__ == "__main__": asyncio.run(_demo())`
     that exercises at least one case per trap, printing which behavior to
     expect so running the file demonstrates the fix.
3. Nothing further to wire up for rules: `.claude/rules/scenarios.md` is
   path-scoped to `Scenario*/*.py`, so it auto-loads for this new file too —
   don't create a per-folder CLAUDE.md or rule file.
4. After writing the file, run `python3 ScenarioN/scenarioN_<slug>.py` to
   confirm it executes cleanly, and report the output.
5. Do not touch any other scenario file. Do not add the new domains to
   CLAUDE.md's domain table unless asked — flag it as a suggestion instead.
