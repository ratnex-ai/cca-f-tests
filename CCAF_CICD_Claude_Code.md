# CI/CD with Claude Code — CCA-F Exam Notes

How Claude Code runs inside a pipeline (GitHub Actions, GitLab CI, Jenkins), and the exam concepts that come with it: headless mode, sessions across steps, structured output for PR comments, guardrails, permissions, and when *not* to run it synchronously.

**Exam mapping:** this is the "Claude Code for CI/CD" scenario. It mainly tests **Domain 3** (Claude Code Configuration & Workflows), with **Domain 1** (sessions, guardrails), **Domain 4** (structured output) and **Domain 5** (context, reliability) showing up in the answer options.

---

## 1. The vocabulary: job, step, session, context

| Term | What it is | Lifetime | Exam point |
|---|---|---|---|
| **Job** | A unit of work in the pipeline, run on **one runner** (one machine/container, one workspace). | One pipeline run. | Different jobs run on different runners — they share **nothing** on disk unless you pass artifacts. |
| **Step** | One command or action inside a job. Steps run **sequentially** on the same runner. | One command. | Steps share the runner's filesystem, so a later step *can* resume a session an earlier step created. |
| **Session** | Claude Code's conversation state: `session_id`, message history, tool calls and results, findings so far. Stored on the runner's disk (`~/.claude/projects/...`). | Until the runner is torn down. | **Every `claude -p` call starts a new session** unless you pass `--resume <id>`. |
| **Context (config)** | Static project knowledge loaded at the start of *every* session: `CLAUDE.md`, `.claude/rules/`, skills, `settings.json` (permissions, hooks), MCP config. | Lives in the repo. | This is how CI "knows" your testing standards and review criteria — **not** by carrying a session around. |

**Session vs. context in one line:** *context* is what every session starts with (from the repo); a *session* is what one run accumulated (from the conversation).

---

## 2. Headless mode: `-p` is non-negotiable

```bash
claude -p "Review the changes in this PR for security issues"
```

- `-p` / `--print` runs **non-interactively**: one prompt in, result out, process exits.
- Without `-p`, Claude Code starts the interactive UI and **waits for input that never comes** — the job hangs until it times out.

> **Exam trap:** "The pipeline hangs at the Claude step." → The fix is `-p`, not a bigger timeout, not `yes |` piping, not an env var.

### Flags you should recognise

| Flag | Purpose in CI |
|---|---|
| `-p "<prompt>"` | Headless, single run. Can also read the prompt from stdin: `git diff \| claude -p "Review this diff"`. |
| `--output-format text \| json \| stream-json` | `json` returns one object with `result`, `session_id`, `total_cost_usd`, `num_turns`, `is_error`, `subtype` — parse it with `jq`. |
| `--json-schema '<schema>'` | Makes the final answer conform to a JSON Schema (returned in `structured_output`). Use it for machine-readable findings → inline PR comments. |
| `--max-turns N` | Runaway guardrail on agent turns. |
| `--max-budget-usd N` | Runaway guardrail on dollars for the run (print mode). |
| `--allowedTools "Read,Grep,Bash(npm test:*)"` | Tools pre-approved to run without a prompt. In CI there is no human to approve anything. |
| `--disallowedTools "Edit,Write,WebFetch"` | Tools removed entirely — the architectural way to make a review job read-only. |
| `--permission-mode plan \| acceptEdits \| default` | `plan` for analyse-only jobs; `acceptEdits` for a job that is meant to write fixes. |
| `--append-system-prompt "..."` | Add job-specific instructions on top of Claude Code's own system prompt. |
| `--model sonnet` | Pick the model per job (cheap model for triage, stronger for review). |
| `--mcp-config mcp.json` | Give the job MCP tools (issue tracker, internal APIs). |
| `--resume <session_id>` / `-r` | Continue a **specific** earlier session. |
| `--continue` / `-c` | Continue the **most recent** session in the current directory. |
| `--fork-session` | With `--resume`: branch from that session into a new id instead of appending to it. |

Flags evolve between Claude Code versions — check `claude --help` on the version you pin in CI.

---

## 3. Sessions across steps: `--resume` vs. `--continue`

By default each step is a clean slate. To carry one step's analysis into the next, capture the `session_id` and resume it explicitly.

```
JOB: pr_review  (one runner, one filesystem)
│
├─ step 1  checkout
│
├─ step 2  claude -p "Analyze PR diff" --output-format json
│            └── creates session  A  ──► session_id=A saved to $GITHUB_ENV
│
├─ step 3  claude -p --resume A "Generate targeted tests for the risks you found"
│            └── appends to session A (sees step 2's analysis)
│
└─ step 4  claude -p "Review the generated tests"          ◄── NO --resume
             └── creates session  B  (fresh: independent reviewer, see §6)

JOB: lint  (different runner)
└─ claude -p ...  ──► session C   (cannot see A or B: different machine)
```

| | `--resume <id>` | `--continue` |
|---|---|---|
| Which session | Exactly the one you name | "Most recent" in this directory |
| Deterministic? | Yes | No — depends on what ran last |
| Safe with concurrency / multiple Claude calls per job? | **Yes** | **No** — can pick up the wrong session |
| Use in CI | ✅ Always | ❌ Avoid (fine for a human at a terminal) |

**Exam points:**
- Each step = new session unless you `--resume`.
- Prefer `--resume <session_id>` over `--continue` in automation: explicit beats "most recent".
- Sessions live on the runner's disk, so **you cannot resume across jobs** (or across pipeline runs) unless you persist `~/.claude` as an artifact/cache — and usually you shouldn't. Pass *findings* (a JSON file) between jobs instead of a session.
- Resume when it's the **same task** continuing (analysis → tests for that analysis). Start **fresh** when you want an independent judgement (review) or the earlier state is stale.

---

## 4. Example: GitHub Actions — analyse, then generate tests, then review independently

```yaml
name: claude-pr-review
on:
  pull_request:

permissions:
  contents: read
  pull-requests: write          # to post review comments

jobs:
  pr_review:
    runs-on: ubuntu-latest
    timeout-minutes: 20          # pipeline-level backstop, in addition to Claude's own guardrails
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}   # never hard-code the key
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0         # full history so `git diff origin/main...` works

      - name: Install Claude Code
        run: npm install -g @anthropic-ai/claude-code

      # STEP A - analysis. Read-only, structured, capped.
      - name: Analyze PR diff
        run: |
          git diff origin/${{ github.base_ref }}...HEAD > pr.diff
          claude -p "Analyze pr.diff for bugs and security issues. Only report issues in changed lines." \
            --output-format json \
            --json-schema "$(cat .github/claude/findings.schema.json)" \
            --allowedTools "Read,Grep,Glob" \
            --disallowedTools "Edit,Write,Bash,WebFetch" \
            --max-turns 15 \
            --max-budget-usd 2 \
            > analysis.json
          jq -e '.is_error == false' analysis.json      # fail the step if Claude errored
          echo "SESSION_ID=$(jq -r '.session_id' analysis.json)" >> "$GITHUB_ENV"

      # STEP B - same task continued: tests for the risks found in step A.
      - name: Generate targeted tests
        run: |
          claude -p "Write tests that cover the risks you found. Match the existing test style in tests/." \
            --resume "$SESSION_ID" \
            --allowedTools "Read,Grep,Glob,Write,Bash(pytest:*)" \
            --permission-mode acceptEdits \
            --max-turns 20 \
            --max-budget-usd 3 \
            --output-format json > tests.json

      # STEP C - independent review: deliberately NOT resumed.
      - name: Independent review of generated tests
        run: |
          claude -p "Review the uncommitted test changes for weak or duplicate assertions." \
            --allowedTools "Read,Grep,Glob,Bash(git diff:*)" \
            --max-turns 10 \
            --output-format json > review.json

      - name: Post findings as PR comments
        run: python .github/claude/post_comments.py analysis.json review.json
```

An example `findings.schema.json` — the shape your comment-posting script can rely on:

```json
{
  "type": "object",
  "properties": {
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "file":     { "type": "string" },
          "line":     { "type": "integer" },
          "severity": { "type": "string", "enum": ["critical", "high", "medium", "low"] },
          "category": { "type": "string", "enum": ["bug", "security", "performance", "style"] },
          "message":  { "type": "string" },
          "suggested_fix": { "type": "string" }
        },
        "required": ["file", "line", "severity", "category", "message"],
        "additionalProperties": false
      }
    }
  },
  "required": ["findings"],
  "additionalProperties": false
}
```

### The packaged alternative: `anthropics/claude-code-action`

Instead of installing the CLI yourself, the official GitHub Action wraps it (set up with `/install-github-app` from Claude Code). It can respond to `@claude` mentions in PRs/issues or run a fixed prompt:

```yaml
- uses: anthropics/claude-code-action@v1
  with:
    anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
    prompt: "Review this PR for security issues. Only comment on changed lines."
    claude_args: "--max-turns 10 --allowedTools Read,Grep,Glob"
```

Same concepts apply: headless run, scoped tools, guardrails, `CLAUDE.md` for project context.

---

## 5. Guardrails and permissions in CI

There is no human in the loop, so everything a human would normally catch must be **configured**, not hoped for.

| Concern | Lever | Why |
|---|---|---|
| Runaway loop | `--max-turns` | Caps agent turns; a guardrail, not the normal way to finish. |
| Runaway cost | `--max-budget-usd` | Caps dollars per run. `max_tokens`-style limits cap one response, not the run. |
| Hung job | Pipeline `timeout-minutes` | Last-resort backstop outside Claude. |
| Doing too much | `--allowedTools` / `--disallowedTools` | A review job that *can't* `Edit` is guaranteed read-only; a prompt saying "don't edit" isn't. |
| Dangerous commands | Scoped Bash: `Bash(npm test:*)`, not `Bash` | Allows the test runner, not arbitrary shell. |
| Must-always rules | **Hooks** in `.claude/settings.json` (`PreToolUse` to block, `PostToolUse` to check/format) | Hooks are code: they run in `-p` mode too and can't be talked out of it. |
| Secrets | CI secret store → env var | Never in the prompt, the repo, or `CLAUDE.md`. |
| Skipping permissions | `--dangerously-skip-permissions` | Only inside a throwaway, network-restricted container — never on a runner with real credentials. |

**Did a guardrail stop it?** With `--output-format json`, check `is_error` and `subtype` (e.g. `error_max_turns`). A run stopped by a guardrail is a **partial result** — fail the step or label it, never post it as a complete review.

---

## 6. Review quality concepts the exam likes

- **Independent reviewer, fresh session.** Don't ask the session that *wrote* the code (or the tests) to review it — it carries its own reasoning and tends to confirm it. Run the review as a **new `claude -p` call without `--resume`**.
- **`CLAUDE.md` carries the standards.** Testing conventions, review criteria, "what counts as a real issue" go in the repo's `CLAUDE.md` / `.claude/rules/`, so every CI session loads them automatically. Don't paste them into every prompt.
- **Be explicit about what to report.** "Only report bugs and security issues in changed lines; ignore style" beats "be thorough" — vague criteria produce noisy, low-signal comments and developers stop reading them.
- **Large PRs: split the review.** Many files in one pass dilutes attention. Do a **per-file pass** for local issues, then a separate **cross-file integration pass** for interactions between changes.
- **Re-runs must not duplicate comments.** On a new push, give Claude the previous findings (or the existing PR comments) and ask only for new or unresolved issues.
- **Test generation needs the existing tests in context.** Otherwise it re-creates tests that already exist or ignores your fixtures and style.
- **Structured output, then code.** Claude emits JSON (via `--json-schema`); your script posts the comments. Don't ask Claude to "post comments" by improvising shell calls to the GitHub API.

---

## 7. Synchronous vs. batch: when the pipeline should wait

| Situation | Use | Why |
|---|---|---|
| Pre-merge check that **blocks** the PR | Claude Code `-p` in the job (synchronous) | The developer is waiting; you need the answer now. |
| Overnight audit of the whole codebase, weekly tech-debt report, bulk re-classification | **Message Batches API** | ~50% cheaper, but results can take up to 24h with no latency guarantee, and it doesn't run a multi-turn tool loop for you. |

> **Exam trap:** "Use the Batches API for the pre-merge PR check to cut cost." → Wrong. No latency guarantee means the PR could wait up to a day. Batch is for work nobody is waiting on.

---

## 8. Exam-style questions

**Q1.** A CI job runs `claude "review this PR"` and the pipeline hangs until it times out. Fix?
**A.** Add `-p`. Without it Claude Code starts interactive mode and waits for input.

**Q2.** Step 2 needs the analysis Step 1 produced. What's the most reliable way?
**A.** Run Step 1 with `--output-format json`, capture `session_id`, and run Step 2 with `--resume <session_id>`. Not `--continue` (it picks "most recent", which isn't deterministic).

**Q3.** Job `analyze` and job `fix` run on different runners. `fix` uses `--resume` with `analyze`'s session id and fails. Why, and what instead?
**A.** Sessions are stored on the runner's disk; the second runner doesn't have it. Write `analyze`'s findings to a JSON artifact and pass that to `fix` as input.

**Q4.** Your review step must never modify files. Best control?
**A.** Remove write tools (`--disallowedTools "Edit,Write"`, or an `--allowedTools` list without them). A prompt instruction alone is not a guarantee.

**Q5.** Review comments are inconsistent and hard to post inline. Fix?
**A.** `--output-format json` plus `--json-schema` with `file`, `line`, `severity`, `message`; a script posts the comments from that JSON.

**Q6.** The same agent session generated the code and then reviewed it, missing obvious issues. Fix?
**A.** Review in a **fresh, independent session** (no `--resume`), with explicit review criteria.

**Q7.** Each new push re-posts the same 12 comments. Fix?
**A.** Provide the prior findings/existing comments as context and ask only for new or still-unresolved issues.

**Q8.** Nightly full-repo security audit; nobody waits for it. Cheapest suitable option?
**A.** Message Batches API — latency doesn't matter here and it's about half the cost.

---

## 9. Checklist

1. `-p` on every Claude call in CI.
2. `--output-format json` → check `is_error` / `subtype`, read `session_id` and `total_cost_usd`.
3. `--json-schema` when a script consumes the output.
4. `--max-turns` + `--max-budget-usd` + a pipeline timeout.
5. `--allowedTools` / `--disallowedTools` scoped to the job's purpose; scoped `Bash(...)` patterns.
6. Hooks in `.claude/settings.json` for rules that must always hold.
7. Project standards in `CLAUDE.md`, not repeated in prompts.
8. `--resume <id>` within a job for the same task; fresh session for independent review; artifacts (not sessions) across jobs.
9. Synchronous for blocking checks, Batches API for nobody-is-waiting work.
10. API key from the CI secret store only.

---

## 10. The six CCA-F exam scenarios (for context)

The exam draws its scenario questions from a fixed set of six (a sitting uses a subset):

1. Customer Support Resolution Agent — see [Scenario1/](Scenario1/)
2. Code Generation with Claude Code
3. Multi-Agent Research System — see [Scenario3/](Scenario3/)
4. Developer Productivity with Claude
5. **Claude Code for Continuous Integration** — this file
6. Structured Data Extraction — see [Scenario6/](Scenario6/)

*Related notes:* [CCAF_Exam_Summary.md](CCAF_Exam_Summary.md) (Domain 3 for configuration, Domain 1 for `max_budget_usd`), [CCAF_Anti_Patterns.md](CCAF_Anti_Patterns.md), [CCAF_CorrectPractice.md](CCAF_CorrectPractice.md).
