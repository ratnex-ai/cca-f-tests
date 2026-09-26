# CCA-F Exam Prep

Runnable, small-on-purpose examples for the **CCA-F** (Claude Code Agent —
Fundamentals) certification. Every file maps back to a specific point in the
exam blueprint, so you can read or run one file at a time instead of tracing
concepts through a full production codebase.

This repo doubles as a working example of a `.claude/` project setup
(settings, path-scoped rules, a skill, a slash command, and a subagent) —
see [Claude Code project setup](#claude-code-project-setup) below.

## Repo layout

```
01_agentic_loop.py            # numbered "concept" files: one CCA-F sub-domain each
02_mcp_server.py               # (see the "CCA-F Domain N.M" tag in each header)
03_coordinator_subagents.py
04_structured_output.py
05_delegation_payload.py
concepts.py                    # cross-domain reference, organized by SECTION
Scenario1/, Scenario3/, Scenario6/   # exam-style scenario prompts, one folder each
.claude/                       # Claude Code project setup — see below
```

## The two kinds of example here

**Numbered concept files** (`01_agentic_loop.py`, `02_mcp_server.py`, ...)
call the *real* `anthropic` / `claude-agent-sdk` / `mcp` packages against a
live API key, one CCA-F domain sub-point per file.

**`ScenarioN/` files** simulate an exam-style scenario end to end. Each one
has two parts:

- **SECTION A** — a commented-out reference snippet showing how the scenario
  maps to the real SDK (`AgentDefinition`, `ClaudeAgentOptions`,
  `tool_choice`, etc.) — illustrative only, never executed.
- **SECTION B** — a runnable, **stdlib-only** simulation of just the
  orchestration logic (routing, parallel fan-out, retries, partial-failure
  handling), so you can run it with no API key and watch the architecture
  decision play out.

Every trap named in a scenario's header docstring is fixed at a line marked
`# EXAM TRAP #N FIX:` — a trap being a plausible-but-wrong design a naive
implementation would reach for (e.g. "always fan out to every subagent," or
"retry with the exact same prompt on a validation failure").

## Running things

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...   # only needed for files that hit the real API
python3 <file>.py                      # SECTION B simulations need no key at all
```

## Domains covered so far

| Domain | Name | Weight | Where |
|---|---|---|---|
| D1 | Agentic Architecture & Orchestration | 27% | `01_agentic_loop.py`, `03_coordinator_subagents.py`, `05_delegation_payload.py`, `Scenario1/`, `Scenario3/` |
| D2 | Tool Design & MCP Integration | 18% | `02_mcp_server.py`, `concepts.py` |
| D4 | Structured Output & Validation | — | `04_structured_output.py`, `Scenario6/` |
| D5 | Context Management & Reliability | 15% | `Scenario1/`, `concepts.py` |

More domains exist in the full exam blueprint (e.g. D3) that aren't
exercised by a scenario here yet.

## Claude Code project setup

The `.claude/` directory shows a full, working setup of Claude Code's
project-customization primitives, built around this repo's own conventions
so they're grounded in something concrete rather than generic examples:

- **`settings.json`** — scoped permissions (safe to run `python3`, blocked
  from `rm -rf`/force-push) plus three hooks: a `PreToolUse` guard on
  dangerous Bash commands, a `PostToolUse` syntax check after every edit, and
  a `Stop` hook that flags stray `__pycache__` dirs.
- **`rules/scenarios.md`** / **`rules/concept-files.md`** — path-scoped rules
  (via `paths:` frontmatter) that auto-load only when a matching file is
  open, enforcing each convention above without bloating every context.
- **`skills/exam-trap-audit/`** — a skill that audits a scenario file against
  the rubric: is each named trap *structurally* fixed, not just claimed in a
  comment?
- **`commands/new-scenario.md`** — `/new-scenario` scaffolds a new
  `ScenarioN/` file matching the established template.
- **`agents/exam-reviewer.md`** — a read-only subagent (deliberately scoped
  to `Read`/`Grep`/`Glob`/a restricted `Bash`) that grades a scenario file the
  way the exam would — the same tool-scoping principle the scenarios
  themselves teach, applied to the tooling that reviews them.

See the root `CLAUDE.md` for the full set of conventions these enforce.
