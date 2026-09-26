#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — deterministic guardrail.

This is the actual Claude Code mechanism behind the CCA-F Domain 1.5 idea in
concepts.py SECTION 8: "prompt instruction = probabilistic, hook = code."
Telling Claude in CLAUDE.md "never delete scenario files" is a suggestion the
model could ignore under pressure; blocking the Bash call in code, before it
runs, is a guarantee. Same principle as the coordinator/subagent tool-scoping
example in Scenario3 -- enforce the boundary structurally, not by asking nicely.

Exit code 2 blocks the tool call; stderr is surfaced back to Claude as the
reason, so it can adjust instead of retrying the same thing blindly.
"""
import json
import re
import sys

DENY_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bgit\s+push\s+--force\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bformat\s+[a-zA-Z]:",
    r"\bdel\s+/[fsq]",
]


def main() -> int:
    payload = json.load(sys.stdin)
    command = payload.get("tool_input", {}).get("command", "")

    for pattern in DENY_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            print(
                f"Blocked: '{command}' matches deny-pattern '{pattern}'. "
                "This repo's CLAUDE.md convention is to never run destructive "
                "commands against scenario files without explicit confirmation.",
                file=sys.stderr,
            )
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
