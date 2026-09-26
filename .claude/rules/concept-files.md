---
paths:
  - "0*_*.py"
---

# Numbered concept-file rules

This loads whenever Claude reads a root-level `0N_*.py` file (e.g.
`01_agentic_loop.py`). These are a different convention from the `ScenarioN/`
files — don't apply `.claude/rules/scenarios.md` here.

## Rules

1. **Header format:** `NN - TITLE  (CCA-F Domain X.Y)` — a single domain tag,
   no `Primary domains:` line, no `EXAM TRAP` markers, no SECTION A/B split.
   That's the `ScenarioN/` convention; this one is simpler on purpose.
2. **Use the real SDK, not a simulation.** Unlike `ScenarioN/` files, these
   call the actual `anthropic` / `claude-agent-sdk` / `mcp` packages against a
   real API key. No stdlib-only mock is a substitute here — the point is to
   show the real call shape.
3. **State the setup cost in the docstring.** Since the file hits a real API,
   include the `pip install ...` line and `export ANTHROPIC_API_KEY=...` (or
   whatever else it needs) right after the title line.
4. **One concept per file.** Don't merge two domain sub-points into one
   numbered file — add a new `0N_*.py` instead.
5. **Comment the *why*, tied to the domain tag.** A comment should explain
   why this line matters for the named CCA-F domain, not describe what the
   Python does.
