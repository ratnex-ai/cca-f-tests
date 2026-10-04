# CCA-F Common Anti-Patterns

Plausible-but-wrong designs the exam uses as distractors, grouped by exam domain. Companion to [CCAF_Exam_Summary.md](CCAF_Exam_Summary.md).

## Domain 1 — Agentic Architecture & Orchestration

- ✗ Reading Claude's text for phrases like "I'm done," using a fixed number of loops as the main stop condition, or checking content type instead of `stop_reason` — always trust `stop_reason`.
- ✗ Calling subagents one at a time instead of together in one response (much slower).
- ✗ Passing findings between agents as plain prose — silently loses the source. Always pass structured data (the finding + where it came from + when + how confident).
- ✗ Letting a subagent quietly pick one answer when two sources disagree — it should flag the conflict as unresolved and escalate it instead.
- ✗ An incomplete or "siloed" multi-agent result, where each agent's part looks fine alone but was never combined — usually a missing synthesis/aggregation step, not a broken model.
- ✗ Two agents updating the same record at the same time with no locking — use pessimistic locking or an orchestrator-issued token.

## Domain 2 — Tool Design & MCP Integration

- ✗ Tools with overlapping, near-synonym descriptions (e.g. two tools that both sound like "search") — Claude will pick one at random. Make each tool's purpose mutually exclusive.
- ✗ A failed tool silently returning an empty/successful-looking result (hides the failure) — or, the opposite extreme, one failure crashing the whole pipeline. Return a clear structured error instead.
- ✗ Giving one agent far too many tools at once — this degrades its ability to pick the right one. Scope each agent to only the tools it actually needs.
- ✗ Reading every file in a codebase upfront "just in case" — wastes context. Search first, then read only what's actually relevant.

## Domain 3 — Claude Code Configuration & Workflows

- ✗ Defaulting to direct execution "because it feels faster," even on a large or risky change — if it touches multiple files or is hard to undo, use Plan Mode instead.
- ✗ Stuffing a long, multi-step procedure into CLAUDE.md — pollutes every single session. Move it into a Skill that only runs when specifically invoked.
- ✗ Confusing `context: fork` (isolates execution) with `allowed-tools` (pre-approves tools so they run without a permission prompt) — they solve different problems, don't mix them up. And `allowed-tools` doesn't block other tools; use `disallowed-tools` for that.
- ✗ Believing a fabricated CI flag exists (`--batch`, `--ci-mode`, `CLAUDE_HEADLESS`) — none of these exist. Only `-p` / `--print` is valid for non-interactive mode.

## Domain 4 — Prompt Engineering & Structured Output

- ✗ Vague instructions like "be thorough" or "be conservative" — the model doesn't know what you specifically mean.
- ✗ Filtering issues by the model's own confidence score — its confidence is calibrated to its training data, not to what matters for your business.
- ✗ Adding more than about 5 few-shot examples — bloats the prompt and pushes the model toward rigid pattern-matching instead of generalizing.
- ✗ Letting the same session that wrote the code also review it — it tends to defend its own decisions. Always use a separate, independent session for review.
- ✗ Relying on prompt instructions alone for anything that truly must never happen — prompts are suggestions the model can be tricked into ignoring; enforce it in code instead.

## Domain 5 — Context, Reliability & Governance

- ✗ Escalating purely on negative sentiment, task complexity, or low confidence alone — none of these are reliable reasons by themselves.
- ✗ Treating every "no results found" the same way — a genuinely broken lookup (worth retrying) and a real, valid empty result (nothing to retry) need different handling.
- ✗ Averaging conflicting numbers from two sources, or silently picking one — annotate both with their source and escalate the conflict instead.
- ✗ Treating old data as current — always attach when a fact was published and when it was retrieved.
- ✗ Editing CLAUDE.md mid-session, adding live timestamps to the prompt, or switching models mid-session — any of these breaks the prefix match and kills your prompt cache.
- ✗ In a multi-container setup, letting different containers build the prefix slightly differently (e.g. different tool ordering, different config loading order) — this looks like a cache miss even though the content is logically the same. Build the prefix the same way everywhere.

---

**Remember:** across every domain, the exam consistently rewards specific, structured, code-enforced solutions over vague instructions or "try harder" prompting.
