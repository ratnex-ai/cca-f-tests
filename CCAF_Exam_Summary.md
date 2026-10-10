# CCA-F Exam Summary

Key points to remember from the Claude Certified Architect — Foundations cheat sheet, grouped by exam domain.

## Domain 1 — Agentic Architecture & Orchestration

*27% of the exam*

- The agentic loop: send a request, check `stop_reason`, and if it's `tool_use`, run the tool and append the result, then loop again. Only `end_turn` should end the loop.
- The API is stateless — always send the full conversation history. Append the assistant's tool call first, then the user's tool result, and make sure the tool IDs match exactly.
- In multi-agent systems, use a hub-and-spoke design — subagents should never talk to each other, only to the coordinator.
- Only the coordinator should be able to spawn other agents. Regular subagents should not have that ability.
- To run subagents in parallel, call them together in one response.
- Subagents start with no memory of the conversation so far — you must explicitly tell them anything they need to know.
- Hooks (`PreToolUse`, `PostToolUse`) are code, not instructions — use them for anything that must always happen, since prompt-based rules can be ignored.
- For continuing work: **resume** a session if it's the same task (but say what changed), **fork** a session only to try parallel approaches, and **start fresh** with a short summary if old results are outdated.
- Know these reliability patterns by name: **idempotency keys** (same request → same result, no duplicate side-effects), **circuit breaker** (after repeated failures, stop calling and return a fallback for a cooldown period), **checkpointing** (save state after each step so you can resume), and a **validation/safety agent** that reviews a risky output before it's sent.

*Common anti-patterns for this domain: see [CCAF_Anti_Patterns.md](CCAF_Anti_Patterns.md#domain-1--agentic-architecture--orchestration).*

### Task tool & `allowed_tools` (core spawning & scoping mechanisms)

- **Task tool** — the tool a coordinator calls to spawn a subagent. It's passed a definition (description, prompt, tools, model) so the new subagent knows its role and its limits. Only the coordinator (or an explicitly designed hierarchical sub-coordinator) should have access to it — a standard subagent should not, or it could recursively spawn its own subagents.
- **`allowed_tools`** — Coordinator's scope and skill/slash command's frontmatter scope. However, `tools` is agent/sub-agent's scope..

### Cost guardrail — `max_budget_usd` (Agent SDK) vs. the Messages API

- **`max_budget_usd` exists only in the Agent SDK** (`ClaudeAgentOptions`; `maxBudgetUsd` in TypeScript). It's a dollar cap on the whole agent run, across every turn and every subagent.
- **The Messages API has no dollar-budget parameter.** `max_tokens` caps the output of *one* request, not the cost of a whole loop. If you build the loop yourself, you track the cost yourself from `response.usage` and stop the loop when it goes over.
- Like `max_turns`, it's a **runaway guardrail, not the normal way to finish**. The loop should still end on `end_turn`. Hitting the budget means something went wrong (a loop that keeps retrying, too many subagents spawned).
- The budget is checked **between turns**, so a run can go slightly over the cap. It stops *after* the turn that crossed the limit, not partway through it.

**Agent SDK** — one option, and the SDK enforces it:

```python
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

options = ClaudeAgentOptions(
    allowed_tools=["Read", "Grep", "Task"],
    max_turns=30,          # runaway guardrail on turn count
    max_budget_usd=2.00,   # runaway guardrail on dollars (the whole run, subagents included)
)

async for message in query(prompt="Audit the auth module", options=options):
    if isinstance(message, ResultMessage):
        print(message.total_cost_usd)       # what the run actually cost
        if message.subtype == "error_max_budget_usd":
            # Stopped because of the budget, not because the task was done.
            # Report it as a partial result. Don't present it as a success.
            ...
```

**Messages API** — no budget parameter, so you build the guardrail yourself in your own loop:

```python
from anthropic import Anthropic

client = Anthropic()

# USD per 1M tokens (Sonnet-class pricing; check current rates for your model)
PRICE = {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30}
MAX_BUDGET_USD = 2.00

def cost_of(usage) -> float:
    return (
        usage.input_tokens * PRICE["input"]
        + usage.output_tokens * PRICE["output"]
        + (usage.cache_creation_input_tokens or 0) * PRICE["cache_write"]
        + (usage.cache_read_input_tokens or 0) * PRICE["cache_read"]
    ) / 1_000_000

spent = 0.0
messages = [{"role": "user", "content": "Audit the auth module"}]

for turn in range(30):                      # max_turns equivalent
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,                    # caps THIS response only, not the whole run
        tools=tools,
        messages=messages,
    )
    spent += cost_of(response.usage)

    if response.stop_reason == "end_turn":
        break                               # normal exit
    if spent >= MAX_BUDGET_USD:
        raise RuntimeError(f"budget guardrail: ${spent:.2f} spent")   # runaway exit

    messages.append({"role": "assistant", "content": response.content})
    messages.append({"role": "user", "content": run_tools(response.content)})
```

- **Exam trap:** "Set `max_tokens` low to control what the agent costs." That's wrong. `max_tokens` limits each response, but a loop with 50 turns of short responses can still cost a lot. To cap a whole run you need `max_budget_usd` (Agent SDK) or a running total of `usage` that you check in your own loop (Messages API).

## Domain 2 — Tool Design & MCP Integration

*18% of the exam*

- Claude only knows what a tool's description says — clearly state what it does, when to use it, and when not to.
- If Claude keeps picking the wrong tool, first fix the tool names and descriptions before adding more complexity.
- Tool errors should return a clear "isError" flag with a reason, so Claude knows whether it's worth retrying.
- Only retry errors that are temporary (like a timeout) — don't retry errors caused by bad input or missing permissions.
- Use `tool_choice` "any" when you need Claude to always make a structured tool call.
- Keep shared team configuration in the project's `.mcp.json` file (committed to the repo); keep personal settings in your own user-level config file.
- Know the core built-in tools: **grep** searches inside file contents, **glob** finds files by name or path, **read** loads a whole file, **write** replaces a file, **edit** makes a precise change to existing text, and **bash** runs shell commands.
- MCP has three roles: the **Host** app (e.g. Claude Desktop/Code), the **Client** (one connection per server), and the **Server** (exposes tools, resources, and prompts). One client connects to one server.
- MCP authentication is two layers: a token from client to gateway, and a separate token from gateway to the upstream server. **OAuth 2.1 with PKCE** is the production standard — plain API keys are NOT production-ready (no scoping, no expiry, no per-user audit trail).
- Transport choice: **stdio** for local/CLI tools; **HTTP+SSE** for web clients and serverless (stateless-friendly, works with standard HTTP auth headers, but half-duplex); **WebSocket** only when you truly need full-duplex, low-latency communication.

*Common anti-patterns for this domain: see [CCAF_Anti_Patterns.md](CCAF_Anti_Patterns.md#domain-2--tool-design--mcp-integration).*

### Sample tool definition — key properties (Anthropic API format)

- **name** — the unique identifier, e.g. "create_github_issue." This is literally what Claude matches against your description to decide when to call it.
- **description** — the single most important field. State the purpose, when to call it, when NOT to call it, and any prerequisites — this drives tool selection.
- **input_schema** — a JSON Schema object defining the arguments: `type` (usually "object"), `properties` (each parameter's type + description), and `required` (array of mandatory parameter names).
- Optional but valuable constraints inside properties: `enum` (restrict to a fixed set of values, e.g. `["low","medium","high"]`), `maxLength` (prevent oversized input), and a clear description with an example format (e.g. a date).
- A well-designed error response returned by the tool includes: `isError` (true/false, marks this as a failure not a normal result), `isRetryable` (true/false), `errorCategory` (e.g. "rate_limit", "invalid_input"), and a human-readable `message` — this is what tells Claude whether and how to retry.

## Domain 3 — Claude Code Configuration & Workflows

*20% of the exam*

### What each configuration type is for (exam-critical distinction)

- **CLAUDE.md** — persistent, always-loaded context and instructions (architecture, build commands, conventions). At the personal/user level, this is also where your own preferences live (e.g. preferred testing style, commit message format) — private, never shared with the team.
- **Custom Rules** (`.claude/rules/*.md`) — topic- or file-type-specific instructions that only load when you're working with matching files, via a `paths:` pattern in the file's YAML frontmatter. Keeps CLAUDE.md from becoming one giant file.
- **Custom (Slash) Commands** (`.claude/commands/*.md`) — simple, reusable prompts you trigger manually (e.g. `/review`, `/deploy`). Just runs in your current session — no isolation.
- **Skills** (`SKILL.md`) — configurable mini-agents for complex, multi-step procedures. Can run in an isolated sub-session (`context: fork`) so they don't pollute your main session, and can pre-approve the tools they need (`allowed-tools`) or remove tools (`disallowed-tools`). See the frontmatter section below.

- CLAUDE.md works at three levels that all combine together: personal, project (shared with the team), and folder-specific.
- Use the `/memory` command to check exactly what instructions are currently loaded when Claude isn't behaving as expected.
- Use Plan Mode for large, multi-file, hard-to-undo changes. Use direct execution for small, well-understood, easy-to-undo changes.
- For automated pipelines, only the `-p` (or `--print`) flag puts Claude Code into non-interactive mode.
- When reviewing code in CI, start a fresh session rather than reusing the session that wrote the code.
- Use a `.claudeignore` file (works like `.gitignore`) to keep irrelevant files — build output, dependencies, large assets — out of Claude's context entirely.
- Mark only the truly critical CLAUDE.md rules with **IMPORTANT** or **YOU MUST** — it measurably improves compliance, but loses that effect if overused.
- Claude Code has no built-in memory across sessions by default, and long sessions degrade in quality as they fill up — keep sessions focused, use `--max-turns` to cap runaway loops, and follow the **"two-correction rule"**: if you've corrected Claude twice on the same issue, clear the session and restart with a better prompt.

### Frontmatter fields for Skills and Commands

**What frontmatter is:** a block of YAML settings at the very top of a `.md` file, between two `---` lines. Claude Code reads the frontmatter as *configuration* (name, permissions, model, when to run). Everything below the closing `---` is the *prompt*, which is the instructions Claude follows when the skill or command runs.

```markdown
---
description: Explain what this does      ← frontmatter (settings)
---

Do X, then Y.                             ← body (the prompt itself)
```

**Commands and skills are now the same mechanism.** `.claude/commands/deploy.md` and `.claude/skills/deploy/SKILL.md` both create `/deploy` and accept the same frontmatter, with two exceptions: command files can't use `name` (the file name becomes the command name) or `paths`. Command files still work, but skills are preferred for new work because they get their own folder for supporting files (scripts, reference docs, templates).

#### The fields, grouped by what they control

Every field is optional, but `description` is strongly recommended.

| Group | Field | What it does | Example |
|---|---|---|---|
| **Identity** | `name` | The `/name` you type. Defaults to the folder name. *Skills only.* | `name: exam-trap-audit` |
| | `description` | What it does **and when to use it**. Claude reads this to decide whether to load the skill automatically, so trigger phrases matter. | `description: Use when auditing a ScenarioN file...` |
| | `when_to_use` | Extra trigger phrases or example requests, added to the end of `description`. | `when_to_use: "audit scenario", "check the rubric"` |
| **Who can run it** | `disable-model-invocation` | `true` means only *you* can run it (by typing `/name`); Claude can't trigger it on its own. Use it for anything with side effects. | `disable-model-invocation: true` |
| | `user-invocable` | `false` means only *Claude* can use it; it's hidden from the `/` menu. Use it for background reference knowledge. | `user-invocable: false` |
| **Arguments** | `argument-hint` | Hint shown in autocomplete for what to type after the command. | `argument-hint: <number> <slug>` |
| | `arguments` | Gives the arguments names, so the body can say `$issue` instead of `$0`. | `arguments: [issue, branch]` |
| **Tools** | `allowed-tools` | **Pre-approves** these tools (no permission prompt) for the turn it runs in. It does *not* block other tools. | `allowed-tools: Bash(git add *) Bash(git commit *)` |
| | `disallowed-tools` | **Removes** these tools while the skill is active. This is the one that actually restricts. | `disallowed-tools: Write Edit` |
| **Where it runs** | `context` | `fork` runs the skill in an isolated subagent: a fresh context with no conversation history, so it doesn't fill up your main session. | `context: fork` |
| | `agent` | Which subagent type to use with `context: fork`: `Explore`, `Plan`, `general-purpose` (the default), or a custom agent from `.claude/agents/`. | `agent: Explore` |
| | `background` | Only with `context: fork`. Set it to `false` to wait for the result instead of letting the skill run in the background. | `background: false` |
| **Model** | `model` | Overrides the session model while the skill runs (`sonnet`, `opus`, `haiku`, or `inherit`). | `model: haiku` |
| | `effort` | Overrides the thinking effort level (`low` to `max`). | `effort: high` |
| **Auto-activation** | `paths` | Glob patterns: the skill is only offered automatically when you're working on matching files. *Skills only.* | `paths: ["Scenario*/*.py"]` |
| **Other** | `hooks` | Hooks that register when the skill is invoked. | *(see hooks docs)* |
| | `shell` | Which shell runs the `` !`command` `` lines: `bash` (the default) or `powershell`. | `shell: powershell` |

#### Who can invoke it (exam favourite)

| Frontmatter | You can type `/name` | Claude can auto-invoke | Description loaded into Claude's context |
|---|---|---|---|
| *(default)* | ✓ | ✓ | ✓ |
| `disable-model-invocation: true` | ✓ | ✗ | ✗ |
| `user-invocable: false` | ✗ | ✓ | ✓ |

#### Special syntax in the body (below the frontmatter)

- `$ARGUMENTS` — everything you typed after the command name.
- `$0`, `$1`, … — individual arguments, counting from 0. Wrap multi-word values in quotes: `/fix "login bug" high` gives `$0` = `login bug` and `$1` = `high`.
- `$issue` — a named argument from the `arguments:` field.
- `` !`git diff` `` — runs a shell command **before** Claude sees the prompt and pastes the output in its place. Claude sees the result, not the command. If the command fails, the skill doesn't run.
- `${CLAUDE_SKILL_DIR}` — the skill's own folder, for referencing scripts or reference files stored next to `SKILL.md`.

#### Example 1 — a command (`.claude/commands/commit.md`)

You trigger it by hand; it has side effects; it's pre-approved for exactly the git commands it needs.

```markdown
---
description: Stage and commit current changes with a conventional-commit message
argument-hint: [optional scope]
disable-model-invocation: true        # side effects → only you decide when it runs
allowed-tools: Bash(git add *) Bash(git commit *) Bash(git status *)
model: haiku                          # cheap, simple task
---

Current changes:
!`git status --short`

Write a conventional-commit message (scope: $ARGUMENTS), stage the files, and commit.
```

Run it with `/commit auth`.

#### Example 2 — a skill (`.claude/skills/code-audit/SKILL.md`)

Claude can pick it up automatically from its description; it runs isolated so a big read-only review doesn't flood your main session.

```markdown
---
name: code-audit
description: Use when the user asks to audit, review, or "check" a module for bugs. Read-only review; produces a findings list with file:line references.
arguments: [module]
context: fork                         # isolated subagent, fresh context
agent: Explore                        # read-only built-in agent type
disallowed-tools: Write Edit          # actually blocks edits
effort: high
---

Audit the $module module:
1. Find relevant files with Glob/Grep.
2. Read them and list each bug with file:line and a one-line reason.
3. Do not modify anything.
```

Claude may run this on its own when you say "check the payments module for bugs", or you can run `/code-audit payments`.

**Real examples in this repo:** [.claude/commands/new-scenario.md](.claude/commands/new-scenario.md) (`description` + `argument-hint`, uses `$ARGUMENTS`), [.claude/skills/exam-trap-audit/SKILL.md](.claude/skills/exam-trap-audit/SKILL.md) (`name` + a trigger-rich `description`), [.claude/rules/scenarios.md](.claude/rules/scenarios.md) (a *rule*, whose only frontmatter field is `paths`), and [.claude/agents/exam-reviewer.md](.claude/agents/exam-reviewer.md) (a *subagent*. Note that it uses `tools:`, which really is an allow-list that restricts, unlike a skill's `allowed-tools`).

#### Exam traps around frontmatter

- `allowed-tools` **pre-approves**; it does **not** restrict. To take tools away, use `disallowed-tools` on a skill, or `tools:` on a subagent definition.
- `context: fork` (isolates *context*) and `allowed-tools` (pre-approves *tools*) solve different problems. Don't treat one as a replacement for the other.
- A vague `description` ("helps with code") means Claude never auto-invokes the skill. The description is the trigger.
- A deploy/commit/send-message skill without `disable-model-invocation: true` can be triggered by Claude on its own.
- `name` and `paths` don't work in `.claude/commands/` files; move the file to `.claude/skills/<name>/SKILL.md` if you need them.

*Common anti-patterns for this domain: see [CCAF_Anti_Patterns.md](CCAF_Anti_Patterns.md#domain-3--claude-code-configuration--workflows).*

### Messages API vs. Agent SDK vs. Claude Code CLI (in brief)

- **Messages API** — the raw Anthropic API endpoint. You send messages and get a response; you build the agentic loop, tool execution, and context management yourself.
- **Claude Agent SDK** — a library built on top of the Messages API that gives you the agentic loop, tool use, subagents, hooks, and context management out of the box — for building your own custom agents or apps in code.
- **Claude Code CLI** — the ready-made coding agent product, built using the Agent SDK, with built-in tools (Read/Write/Edit/Bash/Grep/Glob), CLAUDE.md, Rules, Slash Commands, Skills, Hooks, and Plan Mode already wired together — used directly from the terminal, no assembly required.
- In short: **Messages API** = raw building block → **Agent SDK** = framework for building your own agent → **Claude Code** = a finished agent product built from both.

## Domain 4 — Prompt Engineering & Structured Output

*18% of the exam*

- Give explicit, named categories of what to always flag and what to always ignore, rather than relying on general wording.
- A few good examples work better than long written instructions for getting consistent results. Two to four well-chosen examples is usually the right amount.
- Use tool calling (structured output) to enforce the right format — it prevents formatting mistakes, but not factual ones.
- Let required fields be "nullable" so Claude can honestly say something is missing, instead of making up a value.
- If Claude keeps failing to find information that genuinely isn't there, stop retrying and send it to a human instead — retries can't invent missing data.
- The Message Batches API is about half the cost but has no guaranteed delivery time and can't handle multi-step tool use — never use it for anything time-sensitive.
- When a platform supports it, native structured-output enforcement (a JSON schema flag) guarantees the output matches your schema at generation time — stronger than prompting for JSON and hoping, which still needs a validation-and-retry loop as a backup.
- A good validation retry loop asks for a fix with the specific error message, requires two consecutive passing checks before trusting the result, and waits a randomized (not fixed) delay between retries so failures don't all retry at once.

### `input_schema` vs. `output_config` — structuring the input vs. the output

The two settings control different things, so don't confuse them:

| | `input_schema` (on a tool) | `output_config.format` (on the request) |
|---|---|---|
| **What it shapes** | The **arguments Claude passes into a tool** | Claude's **final answer** |
| **What it tells Claude** | "When you call this tool, these are the parameters you must provide." | "Your final answer must be JSON that looks like this." |
| **Where it lives** | Inside each entry of the `tools` array | Top-level request parameter |
| **Where the result shows up** | `tool_use` block → `.input` | `text` block containing the JSON |
| **Use it when** | Claude needs to *take an action* (call an API, query a DB) | You just want *data back* in a fixed shape (extraction, classification) |

**`input_schema` example:** structures the tool's input.

```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 1024,
  "tools": [
    {
      "name": "get_weather",
      "description": "Get the current weather for a city. Use when the user asks about weather conditions.",
      "strict": true,
      "input_schema": {
        "type": "object",
        "properties": {
          "city": { "type": "string", "description": "City name, e.g. \"Paris\"" },
          "unit": { "type": "string", "enum": ["celsius", "fahrenheit"] }
        },
        "required": ["city", "unit"],
        "additionalProperties": false
      }
    }
  ],
  "messages": [{ "role": "user", "content": "What's the weather in Paris?" }]
}
```

Claude responds with a tool call whose arguments match the schema:

```json
{ "type": "tool_use", "id": "toolu_01...", "name": "get_weather", "input": { "city": "Paris", "unit": "celsius" } }
```

**`output_config` example:** structures the final answer.

```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 1024,
  "output_config": {
    "format": {
      "type": "json_schema",
      "schema": {
        "type": "object",
        "properties": {
          "name":  { "type": "string" },
          "email": { "type": "string" },
          "plan":  { "type": "string", "enum": ["free", "pro", "enterprise"] }
        },
        "required": ["name", "email", "plan"],
        "additionalProperties": false
      }
    }
  },
  "messages": [{ "role": "user", "content": "Extract: John (john@co.com) signed up for the Enterprise plan." }]
}
```

Claude's final answer is a `text` block containing JSON that matches the schema:

```json
{ "name": "John", "email": "john@co.com", "plan": "enterprise" }
```

- **Guarantee vs. best effort:** `output_config.format` guarantees that the answer matches the schema. A plain `input_schema` is something Claude *usually* follows. Add `"strict": true` to the tool definition (as above) to *guarantee* that the tool arguments match it too.
- Both need `"additionalProperties": false` and a `required` list for guaranteed (strict/structured-output) mode.
- `output_config.format` replaces the older top-level `output_format` parameter, which is deprecated. It also replaces two older tricks: prefilling the assistant turn with `{`, and forcing a fake "output" tool just to get JSON back.
- Neither one makes the *content* correct. They guarantee the shape, not the facts, so you still need validation for values.

### Extended thinking — `max_tokens` vs. `budget_tokens`

- With extended thinking (introduced with Claude 3.7 Sonnet), the model's output has two parts: **thinking tokens** (its step-by-step reasoning) and **final-answer tokens** (the response itself).
- **`thinking.budget_tokens`** — the most Claude can spend on reasoning. It's a ceiling, not a quota, so Claude may use less. Minimum value is 1024.
- **`max_tokens`** — the **total** output budget: thinking + final answer combined.

  ```json
  {
    "model": "claude-3-7-sonnet-20250219",
    "max_tokens": 4000,
    "thinking": {
      "type": "enabled",
      "budget_tokens": 3000
    },
    "messages": [{ "role": "user", "content": "..." }]
  }
  ```

  - Up to 3000 tokens → thinking
  - The remainder (at least 1000 tokens) → final answer
  - Total output ≤ 4000 tokens
- `budget_tokens` must sit **inside** the `thinking` object, not at the top level.
- `max_tokens` must be **greater than** `budget_tokens`, so there's room left for the final answer. If `budget_tokens` is greater than or equal to `max_tokens`, the Messages API rejects the request with a **400 Bad Request**.
- If Claude runs out of tokens while writing the final answer (after thinking), the response is cut off and `stop_reason` is `"max_tokens"` — not `"end_turn"`. Check for it and either raise `max_tokens` or lower the thinking budget.
- On newer models (Claude Opus 4.6 and later), a fixed `budget_tokens` is deprecated or removed. These models use **adaptive thinking** (`"thinking": {"type": "adaptive"}`), and you control how much they think with `output_config.effort` (e.g. `low` / `medium` / `high`) instead. The idea that `max_tokens` caps total output still applies.

*Common anti-patterns for this domain: see [CCAF_Anti_Patterns.md](CCAF_Anti_Patterns.md#domain-4--prompt-engineering--structured-output).*

## Domain 5 — Context, Reliability & Governance

*~17% of the exam*

- The context window is Claude's entire memory for that call. Put the most important facts near the beginning or end — details placed in the middle tend to get overlooked.
- Don't let a summary quietly drop specific numbers or dates — keep a small, always-updated block of key facts instead.
- Only escalate to a human for one of three reasons: the user asked for one, there's a gap in policy, or the agent is genuinely stuck.
- When something fails, report clearly what failed and why — don't pretend it succeeded, and don't let one failure crash the whole process.
- Don't trust a single overall accuracy number — check accuracy separately for each category of data, since a good average can hide one badly-performing group.
- Keep track of where each fact came from as it passes between agents — losing that source link makes it impossible to verify later.
- Prompt caching can cut repeated costs by roughly 90% by matching the exact beginning (prefix) of your prompt — keep stable content (instructions, tool definitions, project config) first, and anything that varies (like the newest message) at the end.
- Simple example of a "prefix": request 1 sends [system prompt] + [tool definitions] + [CLAUDE.md] + "message A"; request 2 sends the same [system prompt] + [tool definitions] + [CLAUDE.md] + "message B". Everything before the final message is identical, so that shared part is the cached prefix — only the new message at the end has to be processed fresh.
- **Where `cache_control` can go:** you mark a cache breakpoint by adding `"cache_control": {"type": "ephemeral"}` to a **tool definition**, a **system** content block, or a **message content block**. Each breakpoint caches everything before it, in the fixed order `tools` → `system` → `messages`.
  - **Max 4 breakpoints per API request.**
  - **Minimum cacheable size:** the cached prefix must be at least **1024 tokens** on Sonnet 4.x-class models (the exact minimum varies by model, from 512 to 4096). A shorter prefix silently isn't cached — no error, just `cache_creation_input_tokens: 0` in the response.
  - In `messages`, `cache_control` goes on a **content block** inside `content`, not on the message object itself — so the message's `content` has to be a list of blocks, not a plain string.
  - Basic shape — one breakpoint on the system prompt:

    ```json
    {
      "model": "claude-sonnet-4-6",
      "max_tokens": 1024,
      "tools": [{ "name": "search_db", "description": "...", "input_schema": { "type": "object", "properties": {} } }],
      "system": [{ "type": "text", "text": "...", "cache_control": { "type": "ephemeral" } }],
      "messages": [{ "role": "user", "content": "..." }]
    }
    ```

  - Example using all 4 breakpoints (the `//` comments are annotations only — remove them for real JSON):

    ```jsonc
    {
      "model": "claude-sonnet-4-6",
      "max_tokens": 1024,
      "tools": [
        {
          "name": "search_db",
          "description": "...",
          "input_schema": { "type": "object", "properties": {} },
          "cache_control": { "type": "ephemeral" }   // BREAKPOINT 1: static tool definitions
        }
      ],
      "system": [
        {
          "type": "text",
          "text": "GLOBAL SYSTEM PROMPT: You are a financial analyst...",
          "cache_control": { "type": "ephemeral" }   // BREAKPOINT 2: shared system rules
        },
        {
          "type": "text",
          "text": "COMPANY 10-K REPORT DATA: [100,000 tokens of static SEC filings...]",
          "cache_control": { "type": "ephemeral" }   // BREAKPOINT 3: large reference document
        }
      ],
      "messages": [
        { "role": "user", "content": "Analyze the revenue figures." },
        { "role": "assistant", "content": "Here is the revenue breakdown..." },
        {
          "role": "user",
          "content": [
            {
              "type": "text",
              "text": "Now summarize operating expenses.",
              "cache_control": { "type": "ephemeral" }   // BREAKPOINT 4: conversation history so far
            }
          ]
        },
        { "role": "assistant", "content": "Operating expenses were..." },
        { "role": "user", "content": "Compare both to Q3 numbers." }   // uncached: the new input
      ]
    }
    ```

  - The same thing through the Python SDK, which builds this JSON request for you:

    ```python
    from anthropic import Anthropic

    client = Anthropic()

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        tools=[{...}],
        system=[{"type": "text", "text": "...", "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "..."}],
    )
    print(response.usage.cache_read_input_tokens)  # > 0 means the cache was hit
    ```
- The cache lives on Anthropic's servers, not in your own application — so in a **multi-container / horizontally-scaled deployment**, every container or instance benefits from the same cache as long as they send an identical prompt prefix. You don't need sticky sessions or your own shared cache store on your end; just keep the prefix consistent across all instances, and any container can get a cache hit from a prefix another container wrote.
- `/compact` summarizes the conversation so far to free up space, but is lossy and still uses some tokens — use it mid-task at a natural breakpoint. `/clear` (or starting a new session) wipes everything for a completely clean slate — use it when switching to an unrelated task.
- Your saved session history and what Claude currently "sees" are not the same thing — the full record is kept, but Claude only works from a trimmed/summarized view of it once the conversation gets long.

*Common anti-patterns for this domain: see [CCAF_Anti_Patterns.md](CCAF_Anti_Patterns.md#domain-5--context-reliability--governance).*

---

**Remember:** across every domain, the exam consistently rewards specific, structured, code-enforced solutions over vague instructions or "try harder" prompting.
