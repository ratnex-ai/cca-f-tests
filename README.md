# CCA-F Exam Prep

Small-on-purpose examples and revision notes for the **CCA-F** (Claude
Certified Architect — Foundations) certification. Every file maps back to a
specific point in the exam blueprint, so you can read or run one file at a
time instead of tracing concepts through a full production codebase.

This repo doubles as a working example of a `.claude/` project setup
(settings, hooks, path-scoped rules, a skill, a slash command and a subagent).
See [Claude Code project setup](#claude-code-project-setup) below.

## Repo layout

```
01_agentic_loop.py               # numbered concept files: one exam sub-point each,
02_mcp_server.py                 #   real SDK calls (see the "CCA-F Domain N.M" tag
06_query_vs_messages_create.py   #   in each header)
07_delegate_tool_parallel.py

Scenario1/   Customer support resolution agent
Scenario2/   Developer productivity with Claude
Scenario3/   Multi-agent research system
Scenario6/   Structured data extraction

agentic_loop_claude_api.py       # standalone walkthroughs
capstone_project.py

CCAF_Exam_Summary.md             # revision notes (see below)
CCAF_CorrectPractice.md
CCAF_Anti_Patterns.md
CCAF_CICD_Claude_Code.md
CCAF_Subagents_in_CI.md

.claude/                         # Claude Code project setup (see below)
CLAUDE.md                        # conventions for anyone (or any agent) adding content
```

## Code examples

### Numbered concept files

Each one calls the **real** `anthropic` / `claude-agent-sdk` / `mcp` packages,
one exam sub-point per file.

| File | Domain | What it shows |
|---|---|---|
| `01_agentic_loop.py` | 1.1 | The agentic loop on the Messages API: `stop_reason`, `tool_use`, tool results, a real SQLite-backed tool. |
| `02_mcp_server.py` | 2.2 / 2.4 | A real MCP server with structured `isError` results, and the Messages API MCP connector. |
| `06_query_vs_messages_create.py` | 1.1 | The same task via `messages.create()` and via Agent SDK `query()`, with every exam-relevant property annotated: tool definitions, `tool_choice`, `ClaudeAgentOptions`, hooks, `max_budget_usd`. When to use each layer. |
| `07_delegate_tool_parallel.py` | 1.2 | The customer support scenario as coordinator + subagents: a hand-built `delegate` tool with parallel fan-out (Messages API), and the same with `AgentDefinition` + an in-process MCP server + a `PreToolUse` refund gate (Agent SDK). |

### `ScenarioN/` files

Each folder takes one of the exam's scenarios and names the **traps** in it:
plausible-but-wrong designs a naive implementation would reach for (e.g.
"always fan out to every subagent", "retry with the exact same prompt on a
validation failure"). Every trap is fixed at a line marked
`# EXAM TRAP #N FIX:`.

| Folder | Scenario | Domains | SECTION B form |
|---|---|---|---|
| `Scenario1/` | Customer support resolution agent | D1, D2, D5 | Real Messages API agent (needs a key) |
| `Scenario2/` | Developer productivity with Claude | D2, D3, D5 | Stdlib simulation, runs with no key |
| `Scenario3/` | Multi-agent research system | D1, D2, D5 | Directional sketch of `query()` calls (read, don't run) |
| `Scenario6/` | Structured data extraction | D4, D5 | Stdlib simulation, runs with no key |

Scenario files have two parts:

- **SECTION A**: a commented-out reference showing how the scenario maps to
  the real SDK (`AgentDefinition`, `ClaudeAgentOptions`, `tool_choice`,
  hooks, ...). Illustrative only, never executed.
- **SECTION B**: either a runnable, **stdlib-only simulation** of the
  orchestration logic (routing, parallel fan-out, retries, partial-failure
  handling), so you can watch the design decision play out with no API key,
  or a short **directional sketch** of the real calls and the data passed
  between agents.

### Standalone walkthroughs

- `agentic_loop_claude_api.py`: a beginner-friendly agentic loop where
  Claude decides which tool to call and when it's done.
- `capstone_project.py`: a multi-agent customer support system tying the
  agentic loop, coordinator pattern, structured context passing and
  escalation together (uses `claude-haiku-4-5`, well under $0.01 per run).

## Revision notes

| File | What it covers |
|---|---|
| [CCAF_Exam_Summary.md](CCAF_Exam_Summary.md) | Key points for all five domains, with examples: agentic loop, tool scoping, `max_budget_usd`, skills/commands frontmatter, structured output, extended thinking, prompt caching, sessions across hosts (`SessionStore`). |
| [CCAF_CorrectPractice.md](CCAF_CorrectPractice.md) | Correct practice per domain, with a concrete example for each. |
| [CCAF_Anti_Patterns.md](CCAF_Anti_Patterns.md) | Common wrong answers per domain. |
| [CCAF_CICD_Claude_Code.md](CCAF_CICD_Claude_Code.md) | Claude Code in CI/CD: `-p`, jobs/steps/sessions, `--resume` vs `--continue`, guardrails, structured PR findings, batch vs synchronous, reviewing 100+ file PRs in per-file passes. |
| [CCAF_Subagents_in_CI.md](CCAF_Subagents_in_CI.md) | Delegating to a subagent inside a CI review: how the `Agent` tool runs it, what context it gets, `.claude/agents/` vs `--agents`, `--bare` mode, model- vs code-driven delegation. |

## Domains covered

| Domain | Name | Weight | Code | Notes |
|---|---|---|---|---|
| D1 | Agentic Architecture & Orchestration | 27% | `01`, `06`, `07`, `Scenario1/`, `Scenario3/`, `capstone_project.py` | all notes files |
| D2 | Tool Design & MCP Integration | 18% | `02`, `06`, `07`, `Scenario1/`, `Scenario2/`, `Scenario3/` | Exam Summary, Correct Practice |
| D3 | Claude Code Configuration & Workflows | 20% | `Scenario2/`, `.claude/` | Exam Summary, CI/CD, Subagents in CI |
| D4 | Prompt Engineering & Structured Output | 18% | `06`, `Scenario6/` | Exam Summary, CI/CD |
| D5 | Context, Reliability & Governance | ~17% | `Scenario1/`, `Scenario2/`, `Scenario3/`, `Scenario6/` | Exam Summary |

Weights are taken from [CCAF_Exam_Summary.md](CCAF_Exam_Summary.md).

## Running things

```bash
pip install anthropic claude-agent-sdk "mcp[cli]"
export ANTHROPIC_API_KEY=sk-ant-...     # PowerShell: $env:ANTHROPIC_API_KEY = "sk-ant-..."

python 01_agentic_loop.py                 # numbered files: real API calls
python Scenario2/scenario2_developer_productivity.py   # simulations: no key needed
python 07_delegate_tool_parallel.py a     # PART A only (b = PART B, default = both)
```

## Claude Code project setup

The `.claude/` directory is a full, working setup of Claude Code's
project-customization primitives, built around this repo's own conventions:

- **`settings.json`**: scoped permissions (safe to run `python3`, blocked
  from `rm -rf` / force-push) plus three hooks backed by `.claude/hooks/`:
  a `PreToolUse` guard on dangerous Bash commands, a `PostToolUse` syntax
  check after every edit, and a `Stop` hook that flags stray `__pycache__`
  dirs.
- **`rules/scenarios.md`** / **`rules/concept-files.md`**: path-scoped rules
  (via `paths:` frontmatter) that load only when a matching file is open,
  enforcing each convention without bloating every context.
- **`skills/exam-trap-audit/`**: audits a scenario file against the rubric.
  Is each named trap *structurally* fixed, not just claimed in a comment?
- **`commands/new-scenario.md`**: `/new-scenario` scaffolds a new
  `ScenarioN/` file from the template.
- **`agents/exam-reviewer.md`**: a read-only subagent (scoped to
  `Read` / `Grep` / `Glob` / a restricted `Bash`) that grades a scenario file
  the way the exam would. It's the same tool-scoping principle the scenarios
  teach, applied to the tooling that reviews them.

See [CLAUDE.md](CLAUDE.md) for the full set of conventions these enforce.
