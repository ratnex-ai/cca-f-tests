# CCA-F Anti-Patterns & Correct Practices

Revised CCA-F anti-pattern cheat sheet. Each item gives the anti-pattern, the exam-relevant correction, and a compact example. Companion to [CCAF_Anti_Patterns.md](CCAF_Anti_Patterns.md) and [CCAF_Exam_Summary.md](CCAF_Exam_Summary.md).

## Domain 1 — Agentic Architecture & Orchestration

### 1. Anti-pattern: Parsing Claude's text or using a fixed loop count to decide when an agent has finished

**Correct practice:** Use the API's `stop_reason` as the authoritative control signal. Continue the agentic loop for `tool_use`, return tool results, and stop on `end_turn`, rather than interpreting phrases such as "I'm done."

**Example:** Claude says "Analysis complete" but returns `stop_reason="tool_use"`. Execute the requested tool and continue instead of terminating the loop.

### 2. Anti-pattern: Calling independent subagents sequentially, even though none depends on another's output

**Correct practice:** Invoke independent subagents together in the same orchestration turn, then aggregate their responses. Use sequential execution only when Agent B genuinely requires Agent A's result.

**Example:** Run Web Research, Document Analysis, and Database Lookup concurrently, then pass all three outputs to the Synthesis Agent.

### 3. Anti-pattern: Passing findings between agents as plain prose without provenance

**Correct practice:** Use structured output containing the finding, source, publication or retrieval time, and confidence or evidence strength. This preserves traceability and lets downstream agents validate citations and resolve contradictions.

**Example:**

```json
{
  "finding": "Revenue was $10M",
  "source": "FY2026 annual report",
  "retrieved_at": "2026-10-08",
  "confidence": 0.95
}
```

### 4. Anti-pattern: Allowing a subagent to silently choose one answer when credible sources disagree

**Correct practice:** Preserve both findings, mark the conflict as unresolved, and escalate it to the coordinator or a human reviewer. A subagent should not average values or select a preferred source without an explicit resolution policy.

**Example:** Source A reports $10M and Source B reports $12M. Return both with provenance and `status: "unresolved_conflict"`.

### 5. Anti-pattern: Returning several correct but disconnected subagent outputs without synthesis

**Correct practice:** Add a coordinator or synthesis stage that combines findings, removes duplication, reconciles compatible information, and highlights unresolved conflicts. Missing synthesis is normally an orchestration-design problem, not a model-quality problem.

**Example:** Research, Security, and Legal agents return separate assessments. A Synthesis Agent produces one recommendation with risks, evidence, and exceptions.

### 6. Anti-pattern: Allowing multiple agents to update the same record concurrently without coordination

**Correct practice:** Protect shared state through pessimistic locking, optimistic concurrency/version checks, or an orchestrator-issued update token. The workflow must detect stale writes instead of allowing one agent to overwrite another.

**Example:** Agent A receives record version 7; Agent B updates it to version 8. Agent A's update is rejected and must reread before retrying.

## Domain 2 — Tool Design & MCP Integration

### 1. Anti-pattern: Defining multiple tools with overlapping, near-synonym descriptions

**Correct practice:** Give every MCP tool a mutually exclusive purpose, precise input schema, and clear "use when" description. Distinct tool boundaries improve selection accuracy and reduce random or inconsistent tool choice.

**Example:** `search_code` searches source files, while `search_product_docs` searches documentation. Avoid two generic tools both described as "search project information."

- **Combine** tools when operations belong to the same domain/entity and differ only by parameters. E.g. tools like `search_pdf`, `search_word`, `search_excel` should be combined into a `search_document` tool with a `filetype` parameter that expects values like `pdf`/`word`/`excel`.
- **Split** into granular tools when operations require different reasoning, permissions, side effects, or selection intent. E.g. a `customer_tool` with an `action` parameter taking `search`/`delete`/`create`/`update` is better split into `search_customer`, `delete_customer`, `create_customer`, `update_customer`.

### 2. Anti-pattern: Returning an empty successful-looking response on failure, or crashing the entire workflow for one tool error

**Correct practice:** Return a structured error that distinguishes failure type, retryability, and relevant diagnostic information. The orchestrator can then retry transient failures, use a fallback, or escalate permanent failures.

**Example:**

```json
{
  "status": "error",
  "code": "DATABASE_UNAVAILABLE",
  "retryable": true
}
```

This must not be returned as `[]`, which could incorrectly mean a valid empty result.

### 3. Anti-pattern: Giving one agent access to every tool in the system

**Correct practice:** Apply least privilege and expose only tools needed for the agent's role. A smaller toolset improves selection accuracy, reduces accidental actions, and limits the impact of prompt injection or model error.

**Example:** A Code Review Agent receives Read, Search, and Comment tools, but not Deploy, Delete, or Production Database tools.

### 4. Anti-pattern: Reading every repository file upfront "just in case"

**Correct practice:** Use progressive retrieval: search or index first, inspect relevant snippets second, and open full files only when needed. This preserves the context window and reduces irrelevant information.

**Example:** Search for `PaymentService` and its references, then read the five matching files instead of loading the entire repository.

## Domain 3 — Claude Code Configuration & Workflows

### 1. Anti-pattern: Using direct execution for a large, risky, multi-file, or difficult-to-reverse change

**Correct practice:** Use Plan Mode to inspect the repository, identify affected files, explain the approach, and surface risks before making changes. Direct execution is better suited to small, well-scoped, reversible tasks.

**Example:** Use Plan Mode before refactoring authentication across 20 files; directly fix a simple typo in one file.

### 2. Anti-pattern: Putting a long, rarely needed, multi-step procedure in CLAUDE.md

**Correct practice:** Keep CLAUDE.md focused on persistent project instructions that should apply broadly. Move task-specific, reusable procedures into Skills so they are loaded only when relevant or invoked.

**Example:** Keep "use NUnit for tests" in CLAUDE.md; place a 20-step security-audit workflow in a dedicated Skill.

### 3. Anti-pattern: Treating `context: fork`, `allowed-tools`, and `disallowed-tools` as equivalent controls

**Correct practice:** Use `context: fork` to isolate execution context, `allowed-tools` to pre-approve specified tools, and `disallowed-tools` to prohibit tools. Pre-approving one tool does not automatically deny every other tool.

**Example:** Run a review Skill in a fork, pre-approve Read and Search, and explicitly disallow Edit and Bash if modification must be prevented.

> `allowed-tools` = **pre-approved** tools. This tells Claude which tools it can use **without asking for approval**.

### 4. Anti-pattern: Using fabricated automation flags such as `--batch`, `--ci-mode`, or `CLAUDE_HEADLESS`

**Correct practice:** Use documented CLI options and design automation around supported non-interactive behavior. For the scenario described, use `-p` or `--print`, and do not assume plausible-sounding flags exist.

**Example:** `claude -p "Review this change and return the findings"`

## Domain 4 — Prompt Engineering & Structured Output

### 1. Anti-pattern: Giving subjective instructions such as "be thorough," "be careful," or "be conservative"

**Correct practice:** Translate subjective goals into explicit scope, decision rules, required fields, and output format. Measurable instructions make results easier to validate and less dependent on interpretation.

**Example:** Replace "Review thoroughly" with: "Report security issues with file, line, severity, evidence, and recommended remediation."

- First improve the prompt. Add few-shot examples only when instructions alone are not enough.
- For Agent to choose correct tool(s), ensure prompts are correct.
- For Messages API, include 2–4 relevant few-shot examples.
- Disable a noisy category. E.g. if a category maintains a 60%+ false-positive rate, disable it.

### 2. Anti-pattern: Filtering business issues solely by the model's self-reported confidence

**Correct practice:** Use deterministic business rules, impact thresholds, validators, and evidence requirements. Treat confidence as optional metadata, not the control that decides whether a material issue is retained.

**Example:** Escalate every payment mismatch above $1,000, even when the model reports high confidence in its explanation.

### 3. Anti-pattern: Adding many repetitive few-shot examples until the prompt becomes bloated

**Correct practice:** Use a small, diverse set of examples that covers important boundaries, edge cases, and expected output. Prefer quality and coverage over repetition, and remove examples that teach the same pattern.

**Example:** Use four examples: valid input, missing field, conflicting sources, and malformed input, rather than 15 nearly identical successful cases.

### 4. Anti-pattern: Letting the same session that generated code perform the final independent review

**Correct practice:** Use a separate session or independent reviewer with fresh context and explicit review criteria. This reduces anchoring on the original approach and increases the chance of detecting flawed assumptions.

**Example:** Session A implements OAuth token validation; Session B receives the diff and security requirements, then reviews it without Session A's reasoning history.

### 5. Anti-pattern: Relying only on prompt instructions for behavior that must never occur

**Correct practice:** Treat prompts as behavioral guidance, not security boundaries. Enforce mandatory restrictions through authorization, schemas, validators, hooks, sandboxing, and tool-level controls.

**Example:** Do not merely prompt "never run `rm -rf`." Block the command with a `PreToolUse` hook or deny it at the execution layer.

## Domain 5 — Context, Reliability & Governance

### 1. Anti-pattern: Escalating solely because sentiment is negative, a task looks complex, or model confidence is low

**Correct practice:** Define explicit escalation rules based on business impact, security, compliance, financial thresholds, or unresolved authoritative conflicts. Sentiment, complexity, and confidence may be signals, but should not be sufficient alone.

**Example:** Escalate suspected personal-data exposure regardless of sentiment; do not escalate an ordinary complaint only because its wording is negative.

### 2. Anti-pattern: Treating every "no results found" response as the same outcome

**Correct practice:** Distinguish a valid empty result from lookup failure using explicit statuses. Retry transient technical failures, but accept a confirmed empty result without repeatedly calling the tool.

**Example:**

- `status: "success", results: []` means nothing matched.
- `status: "error", code: "TIMEOUT"` means the lookup failed and may be retried.

### 3. Anti-pattern: Averaging conflicting numbers or silently selecting one source

**Correct practice:** Keep each value attached to its provenance and mark the discrepancy as unresolved. Reconcile only when an explicit authority hierarchy or business rule identifies the authoritative source.

**Example:** Return "CRM: 1,240 customers" and "Billing: 1,310 customers," then escalate instead of reporting the average 1,275.

### 4. Anti-pattern: Presenting old information as though it were current

**Correct practice:** Attach freshness metadata such as source, publication time, retrieval time, and applicable period. Downstream agents should be able to identify stale information and prefer newer authoritative evidence.

**Example:** Record: "Policy version published January 2026; retrieved October 8, 2026," rather than simply stating "current policy."

### 5. Anti-pattern: Modifying CLAUDE.md, adding dynamic timestamps, or changing models within a cache-sensitive session

**Correct practice:** Keep the cacheable prefix stable and place volatile information after it. Version stable instructions deliberately rather than injecting timestamps or frequently changing configuration into the prefix.

**Example:** Cache stable system instructions and tool definitions; append the current user request and retrieved documents after the cache breakpoint.

### 6. Anti-pattern: Allowing different containers to construct logically identical prompt prefixes in different orders

**Correct practice:** Build prefixes deterministically using the same template, normalization rules, tool order, configuration order, and version across containers. Prompt caching depends on an identical prefix, not merely equivalent meaning.

**Example:** Every container sorts tools by a stable key and loads sections as system → tools → skills → rules; none relies on filesystem iteration order.

## CCA-F Final Memory Chain

1. `stop_reason` → parallel agents → structured provenance → surface conflicts → synthesize outputs → lock shared state
2. Distinct tools → structured errors → least privilege → search before read
3. Plan risky changes → Skills for procedures → fork for isolation → tool lists for permissions → documented CLI
4. Explicit prompts → business rules → focused examples → independent review → controls in code
5. Risk-based escalation → empty vs error → preserve conflicts → freshness metadata → stable prefix → deterministic containers
