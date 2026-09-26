#!/usr/bin/env python3
"""PostToolUse hook (matcher: Edit|Write) — syntax gate on .py files.

Mirrors the PostToolUse pattern from concepts.py SECTION 8
(post_tool_use_hook): normalize/validate the result BEFORE anything downstream
treats it as trustworthy. Here, "downstream" is Claude itself continuing to
build on a file that no longer parses -- catching that immediately, in one
py_compile call, is cheaper than discovering it three edits later.

Exit code 2 feeds stderr back to Claude as the reason, so it can fix the
syntax error immediately instead of the mistake accumulating silently.
"""
import json
import py_compile
import sys


def main() -> int:
    payload = json.load(sys.stdin)
    file_path = payload.get("tool_input", {}).get("file_path", "")

    if not file_path.endswith(".py"):
        return 0

    try:
        py_compile.compile(file_path, doraise=True)
    except py_compile.PyCompileError as exc:
        print(f"Syntax error introduced in {file_path}:\n{exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
