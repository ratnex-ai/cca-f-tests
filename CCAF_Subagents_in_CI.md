# Subagents in CI/CD — CCA-F Exam Notes

How a main agent delegates to a subagent (here, a `test-gap-analyzer`) inside a CI pipeline, and what the exam expects you to know about **how the subagent actually runs**, **what context it gets**, and **how its result gets back**.

**Exam mapping:** the "Claude Code for CI/CD" scenario. Mainly **Domain 1** (subagents, delegation, context passing) and **Domain 3** (Claude Code configuration: `.claude/agents/`, `-p`, tool permissions), with **Domain 4** (structured output for the build gate).

See also: [CCAF_CICD_Claude_Code.md](CCAF_CICD_Claude_Code.md) for headless mode, sessions across steps and guardrails.

---

## 1. The scenario

A PR is opened. CI runs the tests, and some fail. You want Claude to review the PR, and a specialist **`test-gap-analyzer`** subagent to work out which changes lack adequate tests — specific to *this* PR's diff and *these* failures, not generic advice. The build should fail if critical gaps are found.

---

## 2. How a subagent actually executes

This is the core of what the exam tests. In Claude Code and the Agent SDK:

```
CI step: claude -p "Review this PR ..."
   │
   ▼
MAIN AGENT (one session)
   │  reads pr.diff, pytest.log ...
   │
   │  calls the built-in Agent tool:            ◄── delegation is a TOOL CALL
   │    { "subagent_type": "test-gap-analyzer",
   │      "prompt": "<the brief the main agent writes>" }
   ▼
SUBAGENT (fresh, separate context)
   │  sees ONLY: its own system prompt (from its definition)
   │             + the brief in "prompt"
   │  does NOT see: the main agent's conversation, the CI prompt,
   │                anything the main agent read earlier
   │  can use ONLY its own tools (e.g. Read, Grep, Glob)
   │
   │  returns its final answer
   ▼
MAIN AGENT receives it as the Agent tool's result
   │  (only the final answer, not the subagent's intermediate steps)
   ▼
final output → review.json → build gate
```

Key points:

- **Subagents are defined, not spawned as processes.** You declare them (a `.claude/agents/*.md` file for the CLI, or `agents={...}` with `AgentDefinition` in the SDK). At runtime the main agent invokes one through the built-in **`Agent` tool** (called `Task` in older releases and many exam materials).
- **Which subagent runs is decided by the main agent**, from each subagent's **`description`**. Naming it in the prompt ("use the test-gap-analyzer subagent") makes delegation much more likely, but it's still the model's choice — see §6 if it must always run.
- **The brief is the subagent's entire context.** Context isolation is the default. If the main agent doesn't put the diff and the failures (or their file paths) into the brief, the subagent doesn't have them — and you get exactly the generic, false-positive-heavy output the scenario complains about.
- **Only the final answer comes back.** The subagent's file reads and reasoning stay in its own context. This is *why* you use a subagent: reading dozens of test files doesn't flood the main agent's context.
- **Tool scoping is per subagent.** The analyzer gets read-only tools. The main agent needs the `Agent` tool allowed, or the delegation call is denied in headless mode.

---

## 3. What's wrong with the sample (and the exam concept behind each fix)

| Sample shows | Reality | Exam concept |
|---|---|---|
| `from claude import Agent, Coordinator`, subclassing `Agent` | No such library. Real: `claude_agent_sdk` with `AgentDefinition` + `ClaudeAgentOptions` + `query()`, or a `.claude/agents/<name>.md` file for the CLI. | Know the real building blocks. |
| Subagent calls `self.create.message(... output_config=...)` | `output_config` is a **Messages API** parameter. An Agent SDK subagent isn't a Messages API call you make; it's a definition the SDK runs. | Messages API vs. Agent SDK: who owns the loop. |
| Python SDK defines the subagent, then CI runs `claude -p "...delegate to the test-gap-analyzer..."` | The CLI never sees subagents defined in a Python file. For `claude -p`, define it in `.claude/agents/test-gap-analyzer.md` (committed to the repo) or pass `--agents '<json>'`. | Configuration must be where the runtime loads it from. |
| "Delegate via prompt text" is how the subagent gets executed | Prompt text only *encourages* delegation. Execution is an `Agent` tool call the model chooses to make, so `Agent` must be allowed, and the subagent must exist. | Probabilistic vs. deterministic (see §6). |
| `diff[:500]` / `head -n 200 pr.diff` as "summarization" | Silent truncation. Changes after line 200 are never reviewed, and nobody is told. | Don't drop context silently: pass the full file path, chunk large diffs, or record what was left out. |
| Diff and logs pasted into the `-p` argument via `$(...)` | Large diffs hit argument-length limits and break on quotes/`$` in the content. | Write context to files and pass **paths**; the agent reads them with `Read`. Or pipe via stdin. |
| Schema returns one `{module, gap_description, recommendation}` | A PR usually has several gaps. No `additionalProperties: false`, no severity, nothing for a build gate to check. | Design the schema for the consumer: an array of gaps + a count the gate can read. |
| `jq -e '.critical_issues == 0' review.json` | `--output-format json` wraps the answer (`result`, `session_id`, `is_error`, ...). There is no top-level `critical_issues` unless you request a schema; then it's under `.structured_output`. | Know the output envelope; use `--json-schema` for machine-checked fields. |
| `pytest ... \|\| true` then `cat pytest.log` | `pytest.log` is never written. `\|\| true` also hides that tests failed. | Capture output explicitly (`tee`, `--junitxml`) and still fail the build on real test failures. |
| `actions/checkout@v3` then `git diff origin/main...HEAD` | Default shallow clone has no `origin/main` history, so the diff fails. | `fetch-depth: 0`. |
| No install step, no API key | `claude` isn't on the runner, and has no credentials. | Install the CLI; API key from the CI secret store. |
| Coordinator → main agent → subagent | An extra layer that adds nothing here. The main agent already coordinates. | Use the simplest architecture that works; add layers only for a reason. |

---

## 4. Corrected version — Claude Code CLI in CI

### 4a. The subagent definition (committed to the repo)

`.claude/agents/test-gap-analyzer.md`:

```markdown
---
name: test-gap-analyzer
description: >
  Finds missing or inadequate test coverage for the specific changes in a PR,
  using the PR diff and the CI test failures. Use when reviewing a PR that has
  failing tests or untested changes. Read-only.
tools: Read, Grep, Glob
model: sonnet
---
You analyse test coverage for ONE pull request. Your brief gives you:
- the path to the PR diff
- the path to the test output (failures)
- what the reviewer already noticed

Read the diff and the failures. For every changed function or branch, check the
existing tests under tests/ (use Grep). Report only gaps tied to THIS diff or
THESE failures; never generic advice like "add more tests".

For each gap return: file, function, gap_type (untested | inadequate |
failing), severity (critical | high | medium | low), related_failure (test name
or null), recommendation (a concrete test case to add).
```

What each frontmatter field does for the exam:

- **`description`**: the routing signal the main agent reads to decide to delegate. Be specific about *when* to use it.
- **`tools`**: an allow-list that **restricts** the subagent: read-only here, so it can't "fix" code during a review.
- **`model`**: per-subagent model choice.
- **The body**: the subagent's system prompt. The *per-PR* facts come from the brief, not from here.

### 4b. The workflow

```yaml
name: pr-review
on:
  pull_request:
    paths: ['**/*.py', '**/*.js']

permissions:
  contents: read
  pull-requests: write

jobs:
  code-review:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0                       # needed for origin/main...HEAD

      - name: Install Claude Code
        run: npm install -g @anthropic-ai/claude-code

      - name: Collect PR diff
        run: git diff origin/${{ github.base_ref }}...HEAD > pr.diff

      - name: Run tests (capture output, don't stop the job yet)
        id: tests
        continue-on-error: true                # we still want the review
        run: pytest -q --maxfail=20 2>&1 | tee pytest.log

      - name: Claude review with test-gap-analyzer subagent
        run: |
          claude -p "Review this PR.
          The full diff is in pr.diff and the test output is in pytest.log.
          Read both. Then delegate test-coverage analysis to the
          test-gap-analyzer subagent. Its brief MUST include: the paths
          pr.diff and pytest.log, the names of the failing tests, and the
          changed functions you identified. It cannot see this conversation.
          Combine its gaps with your own review findings." \
            --allowedTools "Read,Grep,Glob,Agent" \
            --disallowedTools "Edit,Write,Bash,WebFetch,WebSearch" \
            --output-format json \
            --json-schema "$(cat .github/claude/review.schema.json)" \
            --max-turns 25 \
            --max-budget-usd 3 \
            > review.json
          jq -e '.is_error == false' review.json

      - name: Fail on critical gaps
        run: jq -e '.structured_output.critical_count == 0' review.json

      - name: Fail if tests failed
        if: steps.tests.outcome == 'failure'
        run: exit 1
```

Notes on the choices:

- **Context goes in files, not the command line.** `pr.diff` and `pytest.log` stay complete. The prompt passes *paths*, and both agents read them with `Read`. Nothing is truncated or mangled by shell quoting.
- **The prompt tells the main agent what the brief must contain.** That's the lever you have over what the subagent receives.
- **`Agent` is in `--allowedTools`**, otherwise the delegation call is denied (no human to approve it in CI).
- **Write tools are disallowed.** A review job is read-only by architecture, not by request.
- **The gate reads `.structured_output`**, which exists because `--json-schema` was passed.

`.github/claude/review.schema.json`:

```json
{
  "type": "object",
  "properties": {
    "critical_count": { "type": "integer" },
    "gaps": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "file":            { "type": "string" },
          "function":        { "type": "string" },
          "gap_type":        { "type": "string", "enum": ["untested", "inadequate", "failing"] },
          "severity":        { "type": "string", "enum": ["critical", "high", "medium", "low"] },
          "related_failure": { "type": ["string", "null"] },
          "recommendation":  { "type": "string" }
        },
        "required": ["file", "function", "gap_type", "severity", "recommendation"],
        "additionalProperties": false
      }
    }
  },
  "required": ["critical_count", "gaps"],
  "additionalProperties": false
}
```

---

## 5. The same thing with the Agent SDK (Python)

Directional, not a drop-in script. Use this when the review runs from your own Python service rather than the `claude` CLI.

```python
import asyncio, json
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ResultMessage, query

TEST_GAP_ANALYZER = AgentDefinition(
    description=("Finds missing or inadequate test coverage for the specific "
                 "changes in a PR, using the PR diff and CI test failures. "
                 "Read-only."),
    prompt=("You analyse test coverage for ONE pull request. Your brief gives "
            "the diff path, the test-output path and the changed functions. "
            "Report only gaps tied to THIS diff or THESE failures, as JSON."),
    tools=["Read", "Grep", "Glob"],     # subagent scope: read-only
    model="sonnet",
)

options = ClaudeAgentOptions(
    agents={"test-gap-analyzer": TEST_GAP_ANALYZER},   # SDK equivalent of .claude/agents/
    allowed_tools=["Read", "Grep", "Glob", "Agent"],   # main agent: read + delegate
    disallowed_tools=["Edit", "Write", "Bash"],
    cwd="/path/to/checkout",            # where pr.diff and pytest.log live
    max_turns=25,
    max_budget_usd=3.00,
)

PROMPT = """Review this PR. The diff is in pr.diff, the test output in pytest.log.
Delegate test-coverage analysis to the test-gap-analyzer subagent. Its brief
must include both paths, the failing test names and the changed functions.
Return JSON: {"critical_count": int, "gaps": [...]}"""

async def review() -> dict:
    async for message in query(prompt=PROMPT, options=options):
        if isinstance(message, ResultMessage):
            if message.subtype != "success":           # e.g. error_max_turns
                raise RuntimeError(f"review incomplete: {message.subtype}")
            return json.loads(message.result)          # validate before trusting it

result = asyncio.run(review())
```

Mapping between the two:

| Claude Code CLI | Agent SDK |
|---|---|
| `.claude/agents/test-gap-analyzer.md` (or `--agents` JSON) | `agents={"test-gap-analyzer": AgentDefinition(...)}` |
| frontmatter `description`, `tools`, `model` + body | `AgentDefinition(description, tools, model, prompt)` |
| `--allowedTools "...,Agent"` | `allowed_tools=[..., "Agent"]` |
| `--max-turns`, `--max-budget-usd` | `max_turns=`, `max_budget_usd=` |
| `--output-format json` → `.is_error`, `.result` | `ResultMessage.subtype`, `.result` |

---

## 6. Model-driven vs. code-driven delegation

| | Model-driven (above) | Code-driven |
|---|---|---|
| How | Main agent decides to call the `Agent` tool | Your pipeline runs the analyzer as its own `claude -p` step (or its own `query()`) |
| Runs every time? | Usually, not guaranteed | **Always** |
| Brief written by | The main agent | You, in the workflow: exactly the files and facts you choose |
| Use when | Delegation is a judgement call (only some PRs need it) | The analysis is a **required** check |

Code-driven version of the analyzer step:

```yaml
- name: Test-gap analysis (always runs)
  run: |
    claude -p "Analyse test gaps for this PR. Diff: pr.diff. Test output: pytest.log.
    Report only gaps tied to this diff or these failures." \
      --append-system-prompt "$(awk 'n>=2; /^---$/{n++}' .claude/agents/test-gap-analyzer.md)" \
      --allowedTools "Read,Grep,Glob" \
      --output-format json --json-schema "$(cat .github/claude/review.schema.json)" \
      > gaps.json
```

> **Exam pattern:** if something *must* happen every time, don't rely on the model choosing to do it. Make it a pipeline step, a hook, or a code path. Prompt instructions are for judgement calls.

---

## 7. Passing context to a subagent: checklist

What the brief should carry for this scenario:

- **The PR's changes:** path to the full diff (not a truncated copy), plus the changed functions the main agent identified.
- **The failures:** failing test names and error messages (or the path to the full log / JUnit XML).
- **Where the existing tests are**, so it doesn't recommend tests that already exist.
- **What's already been found**, so it doesn't duplicate the main agent's findings.
- **The expected output shape**, matching what the main agent will merge.

How to pass it:

- **Files + paths** when content is large and the subagent has `Read` with the same working directory. Complete and cheap.
- **Inline text** when it's small, or when the subagent has no file tools (e.g. a synthesis-style subagent with `tools: []`).
- **Never silently truncate.** If you must cut, say what was cut.

---

## 8. Exam-style questions

**Q1.** The test-gap-analyzer returns generic advice ("add more unit tests") unrelated to the PR. Most likely cause?
**A.** Its brief didn't contain the diff and failures. Subagents don't inherit the main agent's context, so whatever isn't in the brief doesn't exist for it.

**Q2.** A subagent is defined in your Python SDK code, but `claude -p` in CI never delegates to it. Why?
**A.** The CLI only loads subagents from `.claude/agents/` (or `--agents`). It doesn't see definitions in your Python code.

**Q3.** In CI the main agent tries to delegate, but the call is denied. Fix?
**A.** Allow the `Agent` tool (`Task` in older versions) in `--allowedTools`. There's no human to approve it at runtime.

**Q4.** The analysis must run on every PR, but sometimes the main agent skips delegating. Fix?
**A.** Make it code-driven: run the analyzer as its own pipeline step instead of relying on the model's choice.

**Q5.** Reviews miss bugs in the bottom half of large PRs. Cause?
**A.** The diff was truncated (`head -n 200`) before being passed in. Pass the full file path, or split the review per file.

**Q6.** The build gate `jq '.critical_issues == 0'` always fails. Why?
**A.** `--output-format json` wraps the answer, so there's no top-level `critical_issues`. Use `--json-schema` and read `.structured_output.critical_count`.

**Q7.** Why use a subagent for test-gap analysis instead of having the main agent do it?
**A.** Context isolation: it reads many test files without filling the main agent's context. It also gets its own restricted, read-only tools and its own model, and returns only a concise result.

**Q8.** The test-gap-analyzer should never modify code. Best control?
**A.** `tools: Read, Grep, Glob` in its definition, which removes write tools. A "don't edit" line in its prompt is not a guarantee.

---

## 9. Shared tools for both: project-scoped `.mcp.json`

If both agents need an MCP server (say, your issue tracker to link gaps to tickets), commit a project-scoped `.mcp.json` so the CLI in CI and developers' local Claude Code load the same config. Secrets stay out of the file via environment-variable expansion:

```json
{
  "mcpServers": {
    "issues": {
      "command": "npx",
      "args": ["-y", "@acme/issues-mcp"],
      "env": { "ISSUES_TOKEN": "${ISSUES_TOKEN}" }
    }
  }
}
```

The tools then appear as `mcp__issues__<tool>`. Add them to the subagent's `tools` list only if it needs them; least privilege applies to MCP tools too.
