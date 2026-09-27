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

### Common anti-patterns to avoid

- ✗ Reading Claude's text for phrases like "I'm done," using a fixed number of loops as the main stop condition, or checking content type instead of `stop_reason` — always trust `stop_reason`.
- ✗ Calling subagents one at a time instead of together in one response (much slower).
- ✗ Passing findings between agents as plain prose — silently loses the source. Always pass structured data (the finding + where it came from + when + how confident).
- ✗ Letting a subagent quietly pick one answer when two sources disagree — it should flag the conflict as unresolved and escalate it instead.
- ✗ An incomplete or "siloed" multi-agent result, where each agent's part looks fine alone but was never combined — usually a missing synthesis/aggregation step, not a broken model.
- ✗ Two agents updating the same record at the same time with no locking — use pessimistic locking or an orchestrator-issued token.

### Task tool & `allowed_tools` (core spawning & scoping mechanisms)

- **Task tool** — the tool a coordinator calls to spawn a subagent. It's passed a definition (description, prompt, `allowed_tools`, model) so the new subagent knows its role and its limits. Only the coordinator (or an explicitly designed hierarchical sub-coordinator) should have access to it — a standard subagent should not, or it could recursively spawn its own subagents.
- **`allowed_tools`** — the least-privilege setting that restricts exactly which tools an agent, subagent, Skill, or slash command is permitted to use. It shows up in a subagent's Task definition, in a Skill's frontmatter, and in a slash command's frontmatter — always scope it to only what that specific task actually needs, nothing more.

## Domain 2 — Tool Design & MCP Integration

*18% of the exam*

- Claude only knows what a tool's description says — clearly state what it does, when to use it, and when not to.
- If Claude keeps picking the wrong tool, first fix the tool names and descriptions before adding more complexity.
- Tool errors should return a clear "this failed" flag with a reason, so Claude knows whether it's worth retrying.
- Only retry errors that are temporary (like a timeout) — don't retry errors caused by bad input or missing permissions.
- Use `tool_choice` "any" when you need Claude to always make a structured tool call.
- Keep shared team configuration in the project's `.mcp.json` file (committed to the repo); keep personal settings in your own user-level config file.
- Know the core built-in tools: **grep** searches inside file contents, **glob** finds files by name or path, **read** loads a whole file, **write** replaces a file, **edit** makes a precise change to existing text, and **bash** runs shell commands.
- MCP has three roles: the **Host** app (e.g. Claude Desktop/Code), the **Client** (one connection per server), and the **Server** (exposes tools, resources, and prompts). One client connects to one server.
- MCP authentication is two layers: a token from client to gateway, and a separate token from gateway to the upstream server. **OAuth 2.1 with PKCE** is the production standard — plain API keys are NOT production-ready (no scoping, no expiry, no per-user audit trail).
- Transport choice: **stdio** for local/CLI tools; **HTTP+SSE** for web clients and serverless (stateless-friendly, works with standard HTTP auth headers, but half-duplex); **WebSocket** only when you truly need full-duplex, low-latency communication.

### Common anti-patterns to avoid

- ✗ Tools with overlapping, near-synonym descriptions (e.g. two tools that both sound like "search") — Claude will pick one at random. Make each tool's purpose mutually exclusive.
- ✗ A failed tool silently returning an empty/successful-looking result (hides the failure) — or, the opposite extreme, one failure crashing the whole pipeline. Return a clear structured error instead.
- ✗ Giving one agent far too many tools at once — this degrades its ability to pick the right one. Scope each agent to only the tools it actually needs.
- ✗ Reading every file in a codebase upfront "just in case" — wastes context. Search first, then read only what's actually relevant.

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
- **Skills** (`SKILL.md`) — configurable mini-agents for complex, multi-step procedures. Can run in an isolated sub-session (`context: fork`) with their own restricted toolset (`allowed-tools`) so they don't pollute your main session.

- CLAUDE.md works at three levels that all combine together: personal, project (shared with the team), and folder-specific.
- Use the `/memory` command to check exactly what instructions are currently loaded when Claude isn't behaving as expected.
- Use Plan Mode for large, multi-file, hard-to-undo changes. Use direct execution for small, well-understood, easy-to-undo changes.
- For automated pipelines, only the `-p` (or `--print`) flag puts Claude Code into non-interactive mode.
- When reviewing code in CI, start a fresh session rather than reusing the session that wrote the code.
- Use a `.claudeignore` file (works like `.gitignore`) to keep irrelevant files — build output, dependencies, large assets — out of Claude's context entirely.
- Mark only the truly critical CLAUDE.md rules with **IMPORTANT** or **YOU MUST** — it measurably improves compliance, but loses that effect if overused.
- Claude Code has no built-in memory across sessions by default, and long sessions degrade in quality as they fill up — keep sessions focused, use `--max-turns` to cap runaway loops, and follow the **"two-correction rule"**: if you've corrected Claude twice on the same issue, clear the session and restart with a better prompt.

### Common anti-patterns to avoid

- ✗ Defaulting to direct execution "because it feels faster," even on a large or risky change — if it touches multiple files or is hard to undo, use Plan Mode instead.
- ✗ Stuffing a long, multi-step procedure into CLAUDE.md — pollutes every single session. Move it into a Skill that only runs when specifically invoked.
- ✗ Confusing `context: fork` (isolates execution) with `allowed-tools` (restricts capabilities) — they solve different problems, don't mix them up.
- ✗ Believing a fabricated CI flag exists (`--batch`, `--ci-mode`, `CLAUDE_HEADLESS`) — none of these exist. Only `-p` / `--print` is valid for non-interactive mode.

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

### Common anti-patterns to avoid

- ✗ Vague instructions like "be thorough" or "be conservative" — the model doesn't know what you specifically mean.
- ✗ Filtering issues by the model's own confidence score — its confidence is calibrated to its training data, not to what matters for your business.
- ✗ Adding more than about 5 few-shot examples — bloats the prompt and pushes the model toward rigid pattern-matching instead of generalizing.
- ✗ Letting the same session that wrote the code also review it — it tends to defend its own decisions. Always use a separate, independent session for review.
- ✗ Relying on prompt instructions alone for anything that truly must never happen — prompts are suggestions the model can be tricked into ignoring; enforce it in code instead.

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
- The cache lives on Anthropic's servers, not in your own application — so in a **multi-container / horizontally-scaled deployment**, every container or instance benefits from the same cache as long as they send an identical prompt prefix. You don't need sticky sessions or your own shared cache store on your end; just keep the prefix consistent across all instances, and any container can get a cache hit from a prefix another container wrote.
- `/compact` summarizes the conversation so far to free up space, but is lossy and still uses some tokens — use it mid-task at a natural breakpoint. `/clear` (or starting a new session) wipes everything for a completely clean slate — use it when switching to an unrelated task.
- Your saved session history and what Claude currently "sees" are not the same thing — the full record is kept, but Claude only works from a trimmed/summarized view of it once the conversation gets long.

### Common anti-patterns to avoid

- ✗ Escalating purely on negative sentiment, task complexity, or low confidence alone — none of these are reliable reasons by themselves.
- ✗ Treating every "no results found" the same way — a genuinely broken lookup (worth retrying) and a real, valid empty result (nothing to retry) need different handling.
- ✗ Averaging conflicting numbers from two sources, or silently picking one — annotate both with their source and escalate the conflict instead.
- ✗ Treating old data as current — always attach when a fact was published and when it was retrieved.
- ✗ Editing CLAUDE.md mid-session, adding live timestamps to the prompt, or switching models mid-session — any of these breaks the prefix match and kills your prompt cache.
- ✗ In a multi-container setup, letting different containers build the prefix slightly differently (e.g. different tool ordering, different config loading order) — this looks like a cache miss even though the content is logically the same. Build the prefix the same way everywhere.

---

**Remember:** across every domain, the exam consistently rewards specific, structured, code-enforced solutions over vague instructions or "try harder" prompting.
