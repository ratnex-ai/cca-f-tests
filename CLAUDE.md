# cca-f_tests

Exam-prep code for the **CCA-F** (Claude Code Agent — Fundamentals) certification.
Every file maps one runnable example back to a specific point in the exam
blueprint. This is not a production system — files are deliberately small so
each one isolates a single testable idea.

## Repo layout

```
01_agentic_loop.py            # numbered "concept" files: one CCA-F sub-domain each
02_mcp_server.py               # (see the "CCA-F Domain N.M" tag in each header)
03_coordinator_subagents.py
04_structured_output.py
05_delegation_payload.py
concepts.py                    # cross-domain reference, organized by SECTION
ScenarioN/                     # exam-style scenario prompts, one folder each
  scenarioN_<slug>.py
.claude/
  settings.json                 # permissions + deterministic hooks (see below)
  rules/scenarios.md            # path-scoped rule: auto-loads for any
                                 # Scenario*/*.py file (see "paths:" frontmatter)
  rules/concept-files.md        # path-scoped rule: auto-loads for root-level
                                 # 0N_*.py files — a different, simpler convention
  commands/new-scenario.md      # /new-scenario — scaffold a new ScenarioN file
  skills/exam-trap-audit/       # audits a scenario file against the rubric
  agents/exam-reviewer.md       # read-only subagent that grades a scenario
  hooks/                        # scripts backing settings.json hooks
```

## The `ScenarioN` file convention

Every scenario file (see `Scenario3/scenario3_multi_agent_research.py` for the
canonical example) follows this exact shape — match it when adding a new one,
or just run `/new-scenario` which scaffolds it for you. `.claude/rules/scenarios.md`
restates the rules below as a path-scoped rule (`paths: ["Scenario*/*.py"]`),
so Claude Code auto-loads them whenever it reads any scenario file, rather
than relying on this root file still being in context by then:

1. **Header docstring** — `CCA-F EXAM PREP — Scenario N: <title>`, then a
   `Primary domains: D#, D#` line naming which blueprint domains it tests.
2. **SECTION A — reference snippet (not executed)**. Shows how the scenario
   maps to the *real* `claude-agent-sdk` / Anthropic Messages API — real
   class names (`AgentDefinition`, `ClaudeAgentOptions`, `tool_choice`, etc.),
   commented out, never run. This is what the exam actually asks about.
3. **SECTION B — runnable simulation of the orchestration logic only**.
   stdlib-only (asyncio, dataclasses, enum — no `anthropic` import, no
   network calls), so it executes deterministically and lets you *watch* the
   architecture decision play out (routing, parallel fan-out, retries,
   partial-failure handling) without needing an API key.
4. **`# EXAM TRAP #N FIX:`** comments at the exact line that fixes a named
   trap from the scenario prompt. A trap is a plausible-but-wrong design a
   naive implementation would reach for (e.g. "always fan out to every
   subagent," "let the synthesis step call the web itself," "retry with the
   exact same prompt on a validation failure"). If you add a scenario, name
   its traps in the header and fix each one at a marked line — don't just
   describe the trap in prose.

## Domains referenced so far (per exam blueprint, weight from Scenario 1's header)

| Domain | Name | Weight | Where |
|---|---|---|---|
| D1 | Agentic Architecture & Orchestration | 27% | `01_agentic_loop.py`, `03_coordinator_subagents.py`, `05_delegation_payload.py`, `Scenario1/`, `Scenario3/` |
| D2 | Tool Design & MCP Integration | 18% | `02_mcp_server.py`, `concepts.py` §1/§2/§5 |
| D4 | Structured Output & Validation | — | `04_structured_output.py`, `Scenario6/` |
| D5 | Context Management & Reliability | 15% | `Scenario1/`, `concepts.py` §9 |

There are more domains in the full blueprint (e.g. D3) not yet exercised by a
scenario here — don't assume this table is exhaustive when writing new content.

**Recurring exam theme — tool scoping happens at two levels.** A coordinator's
own `allowed_tools` (what it can do directly) is a different lever from a
subagent's `AgentDefinition.tools` (what that subagent can do once
delegated to). "Always delegate, never do it inline" is just a prompt
suggestion unless the coordinator is *architecturally* prevented from calling
the tool itself — see `Scenario3/scenario3_multi_agent_research.py` SECTION A.
`.claude/agents/exam-reviewer.md` in this repo applies the same principle to
itself (read-only tools, can't edit the scenario it's grading).

## Running things

```
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...     # only needed for the *_agent.py files
                                          # that hit the real API (e.g. Scenario1)
python3 <file>.py                        # SECTION B simulations need no key
```

## Conventions for new content

- SECTION B must stay stdlib-only and network-free — that's what makes it
  runnable as a deterministic teaching demo. If a scenario needs to show real
  SDK wiring, that goes in SECTION A as a commented-out reference only.
- Comment *why* a design choice avoids a specific trap, not what the code
  does line-by-line — the point is to make the trap recognizable on the exam,
  not to document Python syntax.
- Keep files small: one scenario, one set of traps. Don't merge multiple
  scenario prompts into one file.
