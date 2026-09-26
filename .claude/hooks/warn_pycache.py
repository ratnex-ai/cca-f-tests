#!/usr/bin/env python3
"""Stop hook — reminds about stray __pycache__ dirs left in the repo.

Belt-and-suspenders alongside the PYTHONDONTWRITEBYTECODE=1 env var in
settings.json: that stops NEW bytecode caches from being written this
session, but doesn't clean up ones already on disk from before. Runs when
Claude stops responding; only informs (systemMessage), never blocks.
"""
import json
import os
import sys

PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())


def main() -> int:
    json.load(sys.stdin)  # Stop hook input isn't needed here, just drain stdin

    found = []
    for root, dirs, _files in os.walk(PROJECT_DIR):
        if "__pycache__" in dirs:
            found.append(os.path.relpath(os.path.join(root, "__pycache__"), PROJECT_DIR))
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]

    if found:
        print(json.dumps({
            "systemMessage": (
                f"{len(found)} __pycache__ dir(s) left in the repo "
                f"(e.g. {found[0]}) — safe to delete, or run "
                f"'find . -name __pycache__ -exec rm -rf {{}} +'."
            )
        }))

    return 0


if __name__ == "__main__":
    sys.exit(main())
