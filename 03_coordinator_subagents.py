"""
03 - COORDINATOR + SUBAGENTS + HOOKS  (CCA-F Domains 1.2, 1.3, 1.5, 5.3)
=============================================================================
PART A - hub-and-spoke coordinator built directly on the Messages API.
PART B - the same idea with the real Claude Agent SDK, plus real hooks.

    pip install anthropic claude-agent-sdk
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 03_coordinator_subagents.py
=============================================================================
"""

import asyncio
import json
import os

import anthropic

# =============================================================================
# PART A - COORDINATOR ON THE RAW MESSAGES API
# =============================================================================
# The mechanism that makes a subagent a subagent is that you call the API with
# a SEPARATE messages list. Nothing is shared. There is no hidden channel.
# Context isolation is not a feature you enable - it is what you get by default,
# and passing context is the thing you have to do deliberately.

client = anthropic.Anthropic()


SEARCH_AGENT_PROMPT = """You are a research search specialist.
You will be given ONE narrow subtopic. You have no other context.

Return ONLY a JSON array. Each element must be:
  {"claim": "...", "source_url": "...", "published": "YYYY-MM-DD",
   "excerpt": "...", "relevance": 0.0-1.0}

Never return prose or reasoning chains - the downstream agent has a limited
context budget. If you cannot access a source, say so in a "coverage_gap"
element rather than silently omitting the subtopic."""

SYNTHESIS_AGENT_PROMPT = """You are a synthesis specialist.
You receive findings inline. You have NO memory of the agents that produced
them and cannot query anything further.

Rules:
- Preserve every claim -> source_url mapping through to the final report.
- Where two credible sources conflict, present BOTH values with attribution
  and their publication dates. Never silently pick one.
- Add a "coverage" section naming subtopics with no supporting sources.
- Render financial data as tables, narrative findings as prose."""


def run_subagent(system_prompt: str, task_prompt: str,
                 tools: list | None = None) -> str:
    """One subagent invocation = one API call with its OWN messages list.

    Note what is NOT here: no shared history, no memory object. Everything the
    subagent knows arrives through task_prompt.
    """
    kwargs = {
        "model": "claude-opus-5",
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [{"role": "user", "content": task_prompt}],
    }
    if tools:
        kwargs["tools"] = tools
        # Scoped tools only. A synthesis agent given web_search will try to
        # search; a search agent given refund tools will try to refund.
    response = client.messages.create(**kwargs)
    return "".join(b.text for b in response.content if b.type == "text")


def coordinator(query: str, max_refinement_rounds: int = 2) -> str:
    """Hub-and-spoke. Every message between subagents passes through here."""

    # -- 1. DECOMPOSE. Partition the scope so subagents do not duplicate work.
    subtopics = decompose(query)
    print(f"coordinator: decomposed into {len(subtopics)} subtopics")

    # -- 2. DELEGATE. Distinct subtopic per agent = minimal duplication.
    findings: dict[str, str] = {}
    for topic in subtopics:
        findings[topic] = run_subagent(
            SEARCH_AGENT_PROMPT,
            f"Subtopic: {topic}\n\n"
            f"Overall research question, for relevance judgement only: {query}",
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
        )

    # -- 3. AGGREGATE + 4. ITERATIVELY REFINE.
    for round_no in range(max_refinement_rounds):
        report = run_subagent(
            SYNTHESIS_AGENT_PROMPT,
            "Research question: " + query
            + "\n\nFINDINGS (this is your entire context):\n"
            + json.dumps(findings, indent=2),
        )
        gaps = find_gaps(report)
        if not gaps:
            return report
        print(f"coordinator: round {round_no + 1} left {len(gaps)} gaps, "
              f"re-delegating with targeted queries")
        for gap in gaps:
            findings[gap] = run_subagent(
                SEARCH_AGENT_PROMPT,
                f"Targeted follow-up. Earlier coverage of this was thin.\n"
                f"Subtopic: {gap}",
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
            )
    return report


def decompose(query: str) -> list[str]:
    """Ask the model to partition, then parse. Too-narrow decomposition is the
    exam's named risk: it leaves broad topics with incomplete coverage."""
    text = run_subagent(
        "You partition research questions into 3-5 NON-OVERLAPPING subtopics "
        "that together give COMPLETE coverage. Output a JSON array of strings "
        "and nothing else.",
        query,
    )
    return json.loads(text)


def find_gaps(report: str) -> list[str]:
    """The synthesis agent was told to emit a coverage section; read it."""
    text = run_subagent(
        "Read the report. Output a JSON array of subtopic names that the "
        "report itself flags as unsupported or uncovered. Output [] if none. "
        "Output only JSON.",
        report,
    )
    return json.loads(text)


# =============================================================================
# PART B - THE SAME THING WITH THE CLAUDE AGENT SDK, PLUS REAL HOOKS
# =============================================================================
from claude_agent_sdk import (  # noqa: E402  (import here to keep Part A readable)
    AgentDefinition,
    ClaudeAgentOptions,
    HookContext,
    HookMatcher,
    query as agent_query,
)

REFUND_LIMIT_CENTS = 50_000


async def block_oversized_refunds(input_data, tool_use_id, context: HookContext):
    """PreToolUse hook. Runs in YOUR process, before the tool executes.

    This is the deterministic-vs-probabilistic distinction made concrete.
    A system prompt saying "never refund over $500" fails some fraction of the
    time. This cannot: it is an `if` statement.

    Returning permissionDecision "deny" blocks the call; the reason string is
    fed back to the model so it can choose a different path.
    """
    if input_data.get("tool_name") != "mcp__support-tools__process_refund":
        return {}
    amount = input_data.get("tool_input", {}).get("amount_cents", 0)
    if amount > REFUND_LIMIT_CENTS:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"${amount / 100:.2f} exceeds the ${REFUND_LIMIT_CENTS / 100:.2f} "
                    f"authorisation limit. Compile a handoff summary "
                    f"(customer ID, root cause, amount, recommended action) "
                    f"and escalate to a human agent."
                ),
            }
        }
    return {}


async def normalise_timestamps(input_data, tool_use_id, context: HookContext):
    """PostToolUse hook. Runs AFTER the tool, BEFORE the model sees the result.

    Different MCP servers emit Unix ints, ISO strings, numeric status codes.
    Normalising here means the model never has to reason about the difference -
    and never has to be told about it in the prompt.
    """
    import datetime as dt

    result = input_data.get("tool_response")
    if not isinstance(result, dict):
        return {}

    STATUS = {0: "pending", 1: "shipped", 2: "delivered", 3: "refunded"}
    patched = dict(result)
    if isinstance(result.get("placed_at"), int):
        patched["placed_at"] = dt.datetime.fromtimestamp(
            result["placed_at"], tz=dt.timezone.utc
        ).isoformat()
    if isinstance(result.get("status"), int):
        patched["status"] = STATUS.get(result["status"], "unknown")

    # Also TRIM. A 40-field order record where 5 fields matter is 35 fields of
    # context debt on every single turn (Domain 5.1).
    KEEP = {"order_id", "customer_id", "status", "total_cents",
            "placed_at", "refunded", "found"}
    patched = {k: v for k, v in patched.items() if k in KEEP}

    return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "updatedToolResponse": patched}}


async def run_sdk_research(question: str) -> None:
    options = ClaudeAgentOptions(
        # "Agent" is what current Claude Code emits for subagent spawning.
        # The CCA-F exam guide (v1.0) still calls it "Task" - the tool was
        # renamed Task -> Agent in Claude Code v2.1.63. Listing both is safe.
        allowed_tools=["Read", "Grep", "Glob", "WebSearch", "Agent", "Task"],

        agents={
            "searcher": AgentDefinition(
                description=(
                    "Finds primary sources for ONE narrow subtopic. Use when "
                    "a research question needs external evidence gathered."
                ),
                prompt=SEARCH_AGENT_PROMPT,
                tools=["WebSearch", "Read"],   # scoped: cannot write or run bash
                model="sonnet",                # cheap model for the fan-out
            ),
            "synthesiser": AgentDefinition(
                description=(
                    "Merges gathered findings into a final report preserving "
                    "claim-to-source attribution. Use once search is complete."
                ),
                prompt=SYNTHESIS_AGENT_PROMPT,
                tools=["Read"],                # deliberately NOT WebSearch
                model="opus",                  # capable model for the judgement
            ),
        },

        hooks={
            "PreToolUse": [HookMatcher(matcher="mcp__support-tools__.*",
                                       hooks=[block_oversized_refunds])],
            "PostToolUse": [HookMatcher(hooks=[normalise_timestamps])],
            # matcher=None means "every tool". Pipe-separated patterns work
            # too: matcher="Write|Edit".
        },

        # Guardrails, NOT the loop terminator.
        max_turns=30,
        max_budget_usd=2.00,

        setting_sources=["project"],   # loads .claude/ - CLAUDE.md, skills, rules
    )

    async for message in agent_query(
        prompt=(
            f"Research this question: {question}\n\n"
            f"Delegate each subtopic to the searcher agent, then pass the "
            f"COMPLETE findings inline to the synthesiser - it has no memory "
            f"of the searcher's work and cannot see its tool results."
        ),
        options=options,
    ):
        if hasattr(message, "result"):
            print(message.result)


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("set ANTHROPIC_API_KEY first")

    print("=== PART A: raw Messages API coordinator ===")
    print(coordinator("Grid-scale sodium-ion battery deployments in 2026"))

    print("\n=== PART B: Agent SDK ===")
    asyncio.run(run_sdk_research(
        "Grid-scale sodium-ion battery deployments in 2026"))