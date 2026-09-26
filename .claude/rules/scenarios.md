---
paths:
  - "Scenario*/*.py"
---

# Scenario file rules

This loads whenever Claude reads a `.py` file under any `ScenarioN/` folder.
It layers on top of the root `CLAUDE.md`, not instead of it — read that first
for the full convention (SECTION A/B split, domain table, `EXAM TRAP` marker
format).

## Rules that apply to every scenario file

1. **Header contract.** The file's docstring must open with
   `CCA-F EXAM PREP — Scenario N: <Title>` and a `Primary domains: D#, ...`
   line. Only claim a domain the code actually exercises.
2. **SECTION A / SECTION B split is load-bearing, not decorative.**
   SECTION A is a commented-out reference to the real `claude-agent-sdk` /
   Anthropic Messages API — never executed. SECTION B is the runnable
   simulation and must stay **stdlib-only**: no `import anthropic`, no
   `claude_agent_sdk`, no `requests`/`httpx`/`urllib`/raw sockets. If a
   change needs the real SDK to demonstrate something, that belongs in
   SECTION A, not SECTION B.
3. **Every named trap needs a structural fix, not a comment.** Each trap
   listed in the header must have a matching `# EXAM TRAP #k FIX:` comment at
   the line that actually prevents the trap in code — a routing check, a
   scoped function signature, a bounded retry loop. A comment that merely
   asserts the trap is avoided, without code enforcing it, does not satisfy
   this rule. (`Scenario3/scenario3_multi_agent_research.py` traps #1 and #3
   are the canonical example of a real fix.)
4. **Verify before calling it done.** After editing a scenario file, run it
   (`python3 <file>.py`) and cross-check the printed output against each
   claimed trap fix. For anything non-trivial, prefer invoking the
   `exam-reviewer` subagent or the `exam-trap-audit` skill (see
   `.claude/agents/exam-reviewer.md` / `.claude/skills/exam-trap-audit/`) over
   self-certifying.
5. **One file, one scenario.** Don't fold a second scenario prompt or an
   unrelated domain into an existing file — create a new `ScenarioN/` folder
   instead (`/new-scenario` scaffolds it).
6. **Pass structured, typed data between stages — never a flattened string.**
   When one function/stage hands data to the next, use a typed object
   (`@dataclass`, `Enum`) that carries provenance or error detail, not a
   pre-formatted string blob. This is what makes citations, audits, and
   targeted retries possible later. Examples: `Finding`/`SourceProvenance` in
   `Scenario3/scenario3_multi_agent_research.py`; `ValidationError` (field +
   problem, not a bare bool) in `Scenario6/scenario6_structured_extraction.py`.
7. **Every retry/refinement loop needs a bounded budget and a terminal
   state.** A constant like `MAX_REFINEMENT_ROUNDS` or `MAX_RETRY_ATTEMPTS`
   must cap the loop, and there must be an explicit, non-silent outcome for
   when the budget runs out — surface it as a known limitation, or escalate
   it (`requires_human_review` in Scenario6), never loop forever and never
   ship an invalid/incomplete result as if it were clean.
8. **Retry feedback must name the exact field and the exact problem.** A
   generic "that wasn't right, try again" gives a model nothing to act on
   and rarely converges. Feedback should read like
   `"$.total_amount: expected a number, got '$1,240.00'"` — specific enough
   that a corrected resubmission is possible. See Scenario6's
   `build_feedback(..., specific=True)` vs. its own generic-feedback control
   case in the same file.
9. **A failure in one independent step must not abort the whole pipeline.**
   Collect the failure, keep whatever succeeded, and surface the gap
   explicitly in the final output (e.g. a "Known Limitations" section) —
   don't let one subagent/tool error take down results that didn't depend on
   it. See Scenario3's `asyncio.gather(..., return_exceptions=True)` plus its
   `failures` list threaded through to the final report.
10. **Only parallelize steps that are genuinely independent.** Use
    `asyncio.gather` for calls that don't need each other's output (e.g.
    `web_search` and `doc_analysis` in Scenario3); keep a real data
    dependency (e.g. synthesis needs findings first) sequential. Don't
    parallelize for its own sake, and don't serialize independent work out of
    caution — both are gradable mistakes.
11. **Make the exam-relevant decision visible in the printed output, not just
    in a comment.** Use a `trace`/`_trace()`-style running log or explicit
    `print("(expect: ...)")` lines before each demo case, so that *running*
    the file is itself evidence a trap was fixed — matching the verification
    step in rule 4, not just satisfying it by inspection.
