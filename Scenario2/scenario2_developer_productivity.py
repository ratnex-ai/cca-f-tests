"""
CCA-F EXAM PREP — Scenario 2: Developer Productivity with Claude
Primary domains: D2 (Tool Design & MCP Integration), D3 (Claude Code Configuration & Workflows), D5 (Context Management & Reliability)

WHAT THIS FILE IS
------------------
An Agent SDK agent helps engineers explore an unfamiliar legacy codebase,
generate boilerplate, and automate repetitive tasks, using the built-in
tools (Read, Write, Bash, Grep, Glob) plus an MCP code-intelligence server.

This file has two parts:
  SECTION A — a reference snippet (not executed) showing how this maps to the
              real `claude-agent-sdk`: ClaudeAgentOptions with scoped
              built-in tools, an exploration subagent, an MCP server, and a
              PreToolUse hook guarding Bash.
  SECTION B — a runnable simulation of the ORCHESTRATION LOGIC ONLY (no API
              calls, stdlib-only) so you can execute it and watch each
              design decision play out against a fake legacy repo.

The three exam traps are each fixed at a specific line — search for
"EXAM TRAP" to jump to them:
  #1 "understanding the codebase" by reading every file up front
       -> Glob/Grep to narrow, Read only the hits, and run the noisy
          exploration in a subagent that returns a compact summary
  #2 a vague MCP tool description, so the agent keeps using Grep instead
       of the more capable MCP tool
       -> a description that says what it does, WHEN to use it instead
          of Grep, and what it returns
  #3 "never run destructive commands" as a prompt rule, with Bash
       otherwise unrestricted
       -> a PreToolUse hook that allows only known-safe commands and
          denies everything else with a reason the agent can act on

Run it with:  python scenario2_developer_productivity.py
"""

from __future__ import annotations

# ======================================================================
# SECTION A — Reference: real Claude Agent SDK wiring (illustrative only)
# ======================================================================
#
# from claude_agent_sdk import (
#     AgentDefinition, ClaudeAgentOptions, HookMatcher, ResultMessage, query,
# )
#
# # --- EXAM TRAP #3 (real form): Bash guarded by a hook, not a prompt ----
# SAFE_PREFIXES = ("pytest", "ruff check", "python -m black", "git status",
#                  "git diff", "git log")
#
# async def guard_bash(input_data, tool_use_id, context):
#     cmd = input_data["tool_input"]["command"].strip()
#     if cmd.startswith(SAFE_PREFIXES) and not any(c in cmd for c in "&|;`$>"):
#         return {}                                    # allow
#     return {"hookSpecificOutput": {
#         "hookEventName": "PreToolUse",
#         "permissionDecision": "deny",
#         "permissionDecisionReason": (
#             f"'{cmd}' is not on the allow-list. Allowed: {SAFE_PREFIXES}. "
#             "Ask the engineer to run anything else."),
#     }}
#
# options = ClaudeAgentOptions(
#     system_prompt={"type": "preset", "preset": "claude_code",
#                    "append": "Explore before you edit. Follow the "
#                              "conventions in CLAUDE.md for new code."},
#     cwd="/path/to/legacy-repo",
#     # Loads the repo's CLAUDE.md, .claude/rules, skills: this is how
#     # boilerplate matches house conventions without repeating them in
#     # every prompt.
#     setting_sources=["project"],
#
#     # Built-in tools, pre-approved. Bash is SCOPED to patterns, and the
#     # hook below is the second line of defence for anything that slips in.
#     allowed_tools=["Read", "Grep", "Glob", "Write", "Edit",
#                    "Bash(pytest:*)", "Bash(ruff check:*)", "Bash(git diff:*)",
#                    "Agent", "mcp__code-intel__find_references",
#                    "mcp__code-intel__call_graph"],
#     disallowed_tools=["WebFetch", "WebSearch"],
#     permission_mode="acceptEdits",       # boilerplate writes without prompting
#
#     # MCP server: semantic code intelligence (language-server backed).
#     # Its tool DESCRIPTIONS decide whether the agent ever picks it over
#     # Grep - see EXAM TRAP #2 in Section B.
#     mcp_servers={"code-intel": {"type": "stdio", "command": "code-intel-mcp",
#                                 "args": ["--root", "."]}},
#
#     # EXAM TRAP #1 (real form): exploration runs in a subagent. Its dozens
#     # of Grep/Read results stay in ITS context; the main agent receives
#     # only the summary. (Claude Code also ships a built-in Explore agent.)
#     agents={
#         "codebase-explorer": AgentDefinition(
#             description=("Maps an unfamiliar area of the codebase: where a "
#                          "feature lives, entry points, call flow, key "
#                          "files. Use BEFORE editing unfamiliar code. "
#                          "Read-only. Returns a short structured summary."),
#             prompt=("Narrow with Glob and Grep first; Read only files that "
#                     "match. Never read the whole repo. Return at most 10 "
#                     "findings as {file, line, note}."),
#             tools=["Glob", "Grep", "Read",
#                    "mcp__code-intel__find_references"],
#             model="haiku",
#         ),
#     },
#     hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[guard_bash])]},
#     max_turns=40,
#     max_budget_usd=2.00,
# )
#
# async for message in query(
#         prompt="How is an invoice total calculated in the billing module? "
#                "Then add a pytest file for calculate_invoice and run it.",
#         options=options):
#     if isinstance(message, ResultMessage):
#         print(message.result)
#
# Exam points in that snippet:
#   - allowed_tools scopes Bash by pattern; the hook enforces it in code.
#   - The exploration subagent keeps verbose tool output out of the main
#     context (D5), and its tools are read-only.
#   - MCP tools are just more tools to the model: it chooses them from their
#     descriptions, so a weak description = a tool that never gets used (D2).

# ======================================================================
# SECTION B — Runnable simulation of the orchestration logic
# ======================================================================

import asyncio
import fnmatch
import re
from dataclasses import dataclass, field

# ----------------------------------------------------------------------
# A fake legacy repository and a context-window budget
# ----------------------------------------------------------------------

def _filler(n_lines: int, tag: str) -> str:
    return "\n".join(f"# {tag} legacy helper line {i}" for i in range(n_lines))


REPO: dict[str, str] = {
    "billing/invoice.py": (
        "from billing.tax import apply_tax\n"
        "def calculate_invoice(order):\n"
        "    subtotal = sum(i.price * i.qty for i in order.items)\n"
        "    return apply_tax(subtotal, order.region)\n"
    ),
    "billing/tax.py": (
        "RATES = {'EU': 0.2, 'US': 0.07}\n"
        "def apply_tax(amount, region):\n"
        "    return round(amount * (1 + RATES.get(region, 0)), 2)\n"
    ),
    "billing/api.py": (
        "from billing.invoice import calculate_invoice\n"
        "def post_invoice(req):\n"
        "    # TODO: calculate_invoice is slow for large orders\n"
        "    return {'total': calculate_invoice(req.order)}\n"
    ),
    "billing/batch.py": (
        "from billing.invoice import calculate_invoice\n"
        "def nightly(orders):\n"
        "    return [calculate_invoice(o) for o in orders]\n"
    ),
    "docs/notes.py": (
        "MSG = 'see calculate_invoice in the wiki'   # string, not a call\n"
    ),
}
# ...plus the bulk of a legacy repo: lots of code unrelated to billing.
for _i in range(40):
    REPO[f"legacy/module_{_i:02d}.py"] = _filler(120, f"module_{_i:02d}")


def tokens(text: str) -> int:
    return max(1, len(text) // 4)          # rough chars->tokens estimate


@dataclass
class ContextWindow:
    """Tracks what has been loaded into ONE agent's context."""
    owner: str
    budget: int
    used: int = 0
    log: list[str] = field(default_factory=list)

    def add(self, what: str, text: str) -> None:
        self.used += tokens(text)
        self.log.append(f"{what} (+{tokens(text)} tok)")

    @property
    def overflowed(self) -> bool:
        return self.used > self.budget


# The built-in tools, simulated over REPO.
def tool_glob(pattern: str) -> list[str]:
    return sorted(p for p in REPO if fnmatch.fnmatch(p, pattern))


def tool_grep(pattern: str) -> list[tuple[str, int, str]]:
    hits = []
    for path, text in REPO.items():
        for n, line in enumerate(text.splitlines(), 1):
            if re.search(pattern, line):
                hits.append((path, n, line.strip()))
    return hits


def tool_read(path: str) -> str:
    return REPO[path]


# ----------------------------------------------------------------------
# EXAM TRAP #1 — exploring an unfamiliar codebase
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    """What the explorer hands back: structured, small, citable."""
    file: str
    line: int
    note: str


MAIN_CONTEXT_BUDGET = 8_000        # tokens the main agent can spare for this


def naive_explore(question: str) -> ContextWindow:
    """The trap: 'read everything so I understand the codebase'."""
    ctx = ContextWindow("main agent", MAIN_CONTEXT_BUDGET)
    for path in REPO:                              # every file, up front
        ctx.add(f"Read {path}", tool_read(path))
    return ctx


def explorer_subagent(symbol: str) -> list[Finding]:
    """Runs in its OWN context. Narrow first, then read only the hits."""
    sub = ContextWindow("codebase-explorer", budget=20_000)
    candidates = tool_glob("billing/*.py")         # cheap: names only
    sub.add("Glob billing/*.py", "\n".join(candidates))
    hits = tool_grep(rf"def {symbol}\b|\b{symbol}\(")
    sub.add(f"Grep {symbol}", "\n".join(f"{p}:{n}:{l}" for p, n, l in hits))
    findings: list[Finding] = []
    for path in sorted({p for p, _, _ in hits}):   # read ONLY files that matched
        sub.add(f"Read {path}", tool_read(path))
    for path, n, line in hits:
        role = "definition" if line.startswith("def ") else "call site"
        findings.append(Finding(path, n, f"{role}: {line}"))
    defn = next(f for f in findings if f.note.startswith("definition"))
    if "apply_tax" in tool_read(defn.file):
        findings.append(Finding("billing/tax.py", 2,
                                "total = subtotal + region tax (RATES table)"))
    print(f"   [explorer] its own context used {sub.used} tok "
          f"across {len(sub.log)} tool calls (stays in the subagent)")
    return findings


def fixed_explore(question: str, symbol: str) -> tuple[ContextWindow, list[Finding]]:
    ctx = ContextWindow("main agent", MAIN_CONTEXT_BUDGET)
    # EXAM TRAP #1 FIX: the main agent delegates exploration and receives
    # ONLY the explorer's structured summary - not the files, not the grep
    # output. Narrow-then-read happens inside explorer_subagent().
    findings = explorer_subagent(symbol)
    summary = "\n".join(f"{f.file}:{f.line} {f.note}" for f in findings)
    ctx.add("explorer summary", summary)
    return ctx, findings


# ----------------------------------------------------------------------
# EXAM TRAP #2 — MCP tool vs. built-in Grep: the description decides
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str


GREP = ToolSpec("Grep", "Search file contents for a regex pattern.")

VAGUE_MCP = ToolSpec("mcp__code-intel__find_references", "Code search tool.")

# EXAM TRAP #2 FIX: say WHAT it does, WHEN to use it (and instead of
# what), and WHAT it returns. The model chooses tools from descriptions;
# this is the only text it has to prefer the MCP tool over Grep.
CLEAR_MCP = ToolSpec(
    "mcp__code-intel__find_references",
    "Find every caller, usage and reference of a function, class or method "
    "using the language server's semantic index. Use this INSTEAD of Grep "
    "when asked who calls / uses / references a symbol: Grep matches text, "
    "so it also hits comments, strings and similarly named symbols. Returns "
    "{file, line, kind} for real references only.",
)

INTENT_WORDS = {"caller", "call", "usage", "use", "reference"}


def _stems(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z_]+", text.lower())}


def choose_tool(task: str, tools: list[ToolSpec]) -> ToolSpec:
    """Stand-in for the model's tool choice: it can only go on the text of
    each description. A tool whose description never mentions the task's
    intent loses to the familiar built-in."""
    wanted = _stems(task) & INTENT_WORDS

    def score(t: ToolSpec) -> int:
        return len(wanted & _stems(t.description))

    best = max(tools, key=score)
    return best if score(best) > 0 else GREP       # default: what it knows


def run_reference_tool(tool: ToolSpec, symbol: str) -> list[str]:
    if tool.name == "Grep":
        # Text match: imports, the definition, a TODO comment, a string.
        return [f"{p}:{n}" for p, n, _ in tool_grep(re.escape(symbol))]
    # Semantic index: actual call sites only.
    return [f"{p}:{n}" for p, n, line in tool_grep(rf"\b{symbol}\(")
            if not line.startswith(("#", "def "))]


# ----------------------------------------------------------------------
# EXAM TRAP #3 — automating repetitive tasks with Bash
# ----------------------------------------------------------------------

SAFE_PREFIXES = ("pytest", "ruff check", "python -m black", "git status",
                 "git diff", "git log")


@dataclass
class HookDecision:
    allow: bool
    reason: str = ""


def prompt_rule_only(command: str) -> HookDecision:
    """The trap: 'never run destructive commands' lives in the system
    prompt. Nothing checks it when the call is actually made."""
    return HookDecision(True, "no check - the prompt asked nicely")


def pre_tool_use_bash_guard(command: str) -> HookDecision:
    # EXAM TRAP #3 FIX: a PreToolUse hook runs as CODE before every Bash
    # call. Allow-list of known-safe commands; chaining/redirection is
    # refused so "pytest && rm -rf ." can't sneak through. The deny reason
    # goes back to the agent so it can choose a safe alternative.
    cmd = command.strip()
    if cmd.startswith(SAFE_PREFIXES) and not any(c in cmd for c in "&|;`$>"):
        return HookDecision(True)
    return HookDecision(False, f"'{cmd}' is not on the allow-list "
                               f"{list(SAFE_PREFIXES)}; ask the engineer to run it.")


BOILERPLATE_TEST = """import pytest
from billing.invoice import calculate_invoice

def test_calculate_invoice_applies_regional_tax(make_order):
    order = make_order(items=[(10.0, 2)], region="EU")
    assert calculate_invoice(order) == 24.0
"""


def automate(task_steps: list[str], guard) -> list[str]:
    """Execute the agent's planned Bash steps through `guard`."""
    trace = []
    for cmd in task_steps:
        decision = guard(cmd)
        if decision.allow:
            trace.append(f"RAN     {cmd}")
        else:
            trace.append(f"DENIED  {cmd}\n          -> fed back to agent: {decision.reason}")
    return trace


# ----------------------------------------------------------------------
# Demo
# ----------------------------------------------------------------------

async def _demo():
    question = "How is an invoice total calculated in the billing module?"

    print("=" * 70)
    print("TRAP #1: exploring an unfamiliar codebase")
    print("(expect: naive full read OVERFLOWS the main context; the fix keeps")
    print(" the main context tiny and still answers the question)")
    print("=" * 70)
    naive = naive_explore(question)
    print(f"   naive : read {len(naive.log)} files, main context used "
          f"{naive.used} / {naive.budget} tok -> "
          f"{'OVERFLOW' if naive.overflowed else 'ok'}")
    fixed, findings = fixed_explore(question, "calculate_invoice")
    print(f"   fixed : main context used {fixed.used} / {fixed.budget} tok -> "
          f"{'OVERFLOW' if fixed.overflowed else 'ok'}")
    for f in findings:
        print(f"           {f.file}:{f.line}  {f.note}")

    print("\n" + "=" * 70)
    print("TRAP #2: 'who calls calculate_invoice?' - MCP tool vs. Grep")
    print("(expect: vague MCP description -> agent picks Grep: 7 text hits incl.")
    print(" imports, the definition, a TODO comment and a string; clear")
    print(" description -> MCP tool: only the 2 real call sites)")
    print("=" * 70)
    task = "Find all callers of calculate_invoice"
    for label, mcp in (("vague description", VAGUE_MCP),
                       ("clear description", CLEAR_MCP)):
        tool = choose_tool(task, [GREP, mcp])
        hits = run_reference_tool(tool, "calculate_invoice")
        print(f"   {label:17}: chose {tool.name:34} -> {len(hits)} hits {hits}")

    print("\n" + "=" * 70)
    print("TRAP #3: automating 'add a test, run it, clean up'")
    print("(expect: prompt-only rule RUNS the destructive cleanup; the")
    print(" PreToolUse hook allows pytest/ruff and DENIES the rest, with a")
    print(" reason the agent can act on)")
    print("=" * 70)
    REPO["tests/test_invoice.py"] = BOILERPLATE_TEST     # Write (boilerplate)
    print("   Write tests/test_invoice.py (boilerplate, conventions from CLAUDE.md)")
    planned = ["pytest tests/test_invoice.py -q",
               "ruff check billing tests",
               "rm -rf build/ .pytest_cache",
               "pytest -q && git push --force origin main"]
    for label, guard in (("prompt rule only", prompt_rule_only),
                         ("PreToolUse hook", pre_tool_use_bash_guard)):
        print(f"   --- {label} ---")
        for line in automate(planned, guard):
            print(f"   {line}")


if __name__ == "__main__":
    asyncio.run(_demo())
