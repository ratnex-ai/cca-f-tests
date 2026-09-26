"""
CCA-F EXAM PREP -- Scenario 6: Structured Data Extraction
Primary domains: D4, D5 (as specified in the scenario prompt)

WHAT THIS FILE IS
------------------
Claude extracts structured fields from unstructured documents. Output is
validated against a JSON-schema-shaped contract, and the system stays
accurate under two realities: (1) the model sometimes returns invalid
output, and (2) documents are sometimes too long to extract from in one
pass.

This file has two parts:
  SECTION A -- a reference snippet (not executed) showing how this maps to
              the real Anthropic Messages API (tool-based structured
              output + validation-retry messages + the Message Batches
              API).
  SECTION B -- a runnable simulation of the ORCHESTRATION LOGIC ONLY (no
              API calls, stdlib-only) so you can execute it and watch the
              architecture decisions play out: specific vs. generic retry
              feedback, the requires_human_review terminal state,
              per-section vs. single-pass extraction on a long document,
              and the batch-vs-real-time routing decision.

The three exam traps named in the prompt are each fixed at a specific
point -- search for "EXAM TRAP" to jump to them.

Run it with:  python scenario6_structured_extraction.py
"""

from __future__ import annotations

# ======================================================================
# SECTION A -- Reference: real Anthropic Messages API wiring (illustrative only)
# ======================================================================
#
# from anthropic import Anthropic
# client = Anthropic()
#
# EXTRACT_TOOL = {
#     "name": "extract_invoice",
#     "description": "Extract structured invoice fields found in the document text.",
#     "input_schema": {
#         "type": "object",
#         "properties": {
#             "invoice_number": {"type": "string"},
#             "invoice_date": {"type": "string"},
#             "total_amount": {"type": "number"},
#             "vendor_name": {"type": "string"},
#         },
#         "required": ["invoice_number", "invoice_date", "total_amount", "vendor_name"],
#     },
# }
#
# def call_extraction(document_text: str, prior_feedback: str | None = None) -> dict:
#     messages = [{"role": "user", "content": document_text}]
#     if prior_feedback:
#         messages.append({"role": "user", "content": prior_feedback})
#     response = client.messages.create(
#         model="claude-sonnet-5",
#         max_tokens=1024,
#         tools=[EXTRACT_TOOL],
#         tool_choice={"type": "tool", "name": "extract_invoice"},  # forces structured output
#         messages=messages,
#     )
#     tool_call = next(b for b in response.content if b.type == "tool_use")
#     return tool_call.input
#
# # ----------------------------------------------------------------------
# # EXAM TRAP #1: the retry message must name the exact field and the exact
# # problem, e.g.:
# #   "$.total_amount: expected a number, got the string '$1,240.00'.
# #    Strip currency symbols/commas and resubmit the FULL record with a
# #    plain numeric value for this field."
# # A generic "that wasn't right, try again" gives the model nothing
# # actionable -- it will often just resubmit the same (or a differently
# # wrong) answer, burning retry budget without converging.
# # ----------------------------------------------------------------------
#
# # ----------------------------------------------------------------------
# # Message Batches API -- for BULK, LATENCY-TOLERANT processing only.
# # EXAM TRAP #2: Batches have no speed SLA (processing can take up to 24
# # hours, even though it often finishes sooner) -- never route a request
# # that a user or downstream system is synchronously waiting on through
# # Batches, no matter how large the cost savings (~50% discount) look.
# # ----------------------------------------------------------------------
# batch = client.messages.batches.create(
#     requests=[
#         {
#             "custom_id": f"doc-{doc_id}",
#             "params": {
#                 "model": "claude-sonnet-5",
#                 "max_tokens": 1024,
#                 "tools": [EXTRACT_TOOL],
#                 "tool_choice": {"type": "tool", "name": "extract_invoice"},
#                 "messages": [{"role": "user", "content": doc_text}],
#             },
#         }
#         for doc_id, doc_text in documents.items()
#     ]
# )
# # Poll client.messages.batches.retrieve(batch.id) until
# # .processing_status == "ended", then stream
# # client.messages.batches.results(batch.id) to collect per-request output.

# ======================================================================
# SECTION B -- Runnable simulation of the orchestration logic
# ======================================================================

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


# ----------------------------------------------------------------------
# Schema + validation
# ----------------------------------------------------------------------
# KEY CONCEPT (D4): validation happens against an explicit schema contract,
# and every failure is captured as a structured (field, problem) pair --
# never just a boolean pass/fail -- because that's what makes specific
# retry feedback possible downstream.

INVOICE_SCHEMA = {
    "properties": {
        "invoice_number": "string",
        "invoice_date": "string",
        "total_amount": "number",
        "vendor_name": "string",
    },
    "required": ["invoice_number", "invoice_date", "total_amount", "vendor_name"],
}

_TYPE_MAP = {"string": str, "number": (int, float), "array": list}


@dataclass(frozen=True)
class ValidationError:
    field: str
    problem: str

    def __str__(self) -> str:
        return f"$.{self.field}: {self.problem}"


def validate(data: dict, schema: dict) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for f in schema["required"]:
        if f not in data or data[f] in (None, ""):
            errors.append(ValidationError(f, "required field missing"))
    for f, value in data.items():
        expected = schema["properties"].get(f)
        if expected is None or value in (None, ""):
            continue
        py_type = _TYPE_MAP[expected]
        if not isinstance(value, py_type):
            errors.append(ValidationError(
                f, f"expected {expected}, got {type(value).__name__} ({value!r})"
            ))
    return errors


# ----------------------------------------------------------------------
# Mock extraction (stands in for a Claude tool-use call per section)
# ----------------------------------------------------------------------
# A real per-section call is instructed to extract ONLY what's present in
# that section and leave absent fields out, rather than hallucinating
# values for fields the section doesn't contain -- that's what makes the
# later integration pass meaningful instead of just overwriting garbage.

def _regex_field(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else None


def extract_section(section_text: str) -> dict:
    fields: dict = {}
    if v := _regex_field(section_text, r"Invoice #:\s*(\S+)"):
        fields["invoice_number"] = v
    if v := _regex_field(section_text, r"Date Issued:\s*(\S+)"):
        fields["invoice_date"] = v
    if v := _regex_field(section_text, r"Total Due:\s*\$([\d,]+\.\d{2})"):
        fields["total_amount"] = v  # intentional bug: stays a string, like real OCR noise
    if v := _regex_field(section_text, r"Remit To:\s*(.+)"):
        fields["vendor_name"] = v
    return fields


def integrate_sections(partials: list[dict]) -> dict:
    """Multi-pass integration: merge per-section partial records into one.
    A real integration pass would also flag disagreements between sections
    (e.g. two different totals found) for human review instead of silently
    picking one -- omitted here for brevity, called out as a design point."""
    merged: dict = {}
    for p in partials:
        for k, v in p.items():
            merged.setdefault(k, v)
    return merged


# ----------------------------------------------------------------------
# EXAM TRAP #3 FIX: multi-pass (per-section -> integration) instead of one
# call over the whole document
# ----------------------------------------------------------------------
# Simulated here with a hard character-window truncation standing in for
# the real failure mode: a long document exceeds the portion of context an
# extraction call reliably attends to, so fields that only appear later in
# the document get silently dropped from a single-pass extraction.

MAX_SINGLE_PASS_CHARS = 400


def process_single_pass(full_text: str) -> dict:
    truncated = full_text[:MAX_SINGLE_PASS_CHARS]
    return extract_section(truncated)


def process_multi_pass(sections: list[str]) -> dict:
    partials = [extract_section(s) for s in sections]
    return integrate_sections(partials)


# ----------------------------------------------------------------------
# EXAM TRAP #1 FIX + requires_human_review pattern
# ----------------------------------------------------------------------

MAX_RETRY_ATTEMPTS = 3


def build_feedback(error: ValidationError, *, specific: bool) -> str:
    if not specific:
        return "The extraction was invalid. Please try again."
    if "expected" in error.problem:
        return (f"{error}. Convert this field to the exact type the schema "
                 f"requires and resubmit the FULL record -- do not omit "
                 f"other fields.")
    return (f"{error}. Search the document again (including headers/"
            f"footers) for this value and resubmit the FULL record.")


def apply_feedback(data: dict, error: ValidationError, *, specific: bool) -> dict:
    """Stands in for 'what the model does with the feedback message'. This
    is deliberately deterministic so the specific-vs-generic contrast below
    is reproducible: specific feedback that names a fixable field lets the
    mock 'model' correct it; generic feedback gives it nothing to act on,
    so the output doesn't change; and feedback about a field that is
    genuinely absent from every section can't conjure data that was never
    extracted in the first place -- which is exactly why a terminal
    requires_human_review state has to exist."""
    corrected = dict(data)
    if not specific:
        return corrected
    if error.field == "total_amount" and isinstance(corrected.get("total_amount"), str):
        raw = corrected["total_amount"].replace(",", "").replace("$", "")
        try:
            corrected["total_amount"] = float(raw)
        except ValueError:
            pass
    return corrected


@dataclass
class ExtractionOutcome:
    status: str  # "validated" | "requires_human_review"
    data: dict
    attempts: int
    error_history: list[list[ValidationError]] = field(default_factory=list)


HUMAN_REVIEW_QUEUE: list[dict] = []


def extract_with_validation_retry(
    document_id: str, initial_data: dict, schema: dict, *, specific_feedback: bool, trace: list[str]
) -> ExtractionOutcome:
    data = dict(initial_data)
    error_history: list[list[ValidationError]] = []

    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        errors = validate(data, schema)
        error_history.append(errors)
        if not errors:
            trace.append(f"  attempt {attempt}: valid")
            return ExtractionOutcome("validated", data, attempt, error_history)

        trace.append(f"  attempt {attempt}: {len(errors)} error(s) -> {[str(e) for e in errors]}")
        if attempt == MAX_RETRY_ATTEMPTS:
            break

        target = errors[0]
        feedback = build_feedback(target, specific=specific_feedback)
        trace.append(f"    feedback sent ({'specific' if specific_feedback else 'generic'}): {feedback}")
        data = apply_feedback(data, target, specific=specific_feedback)

    # ------------------------------------------------------------
    # Persistent failure after MAX_RETRY_ATTEMPTS: do NOT loop forever and
    # do NOT silently ship invalid data downstream (D5 reliability). Park
    # it for a human, with the full attempt history for audit.
    # ------------------------------------------------------------
    trace.append(f"  retry budget exhausted -> requires_human_review")
    HUMAN_REVIEW_QUEUE.append({
        "document_id": document_id,
        "data": data,
        "error_history": [[str(e) for e in errs] for errs in error_history],
        "attempts": MAX_RETRY_ATTEMPTS,
        "flagged_at": datetime.now(timezone.utc).isoformat(),
    })
    return ExtractionOutcome("requires_human_review", data, MAX_RETRY_ATTEMPTS, error_history)


# ----------------------------------------------------------------------
# EXAM TRAP #2 FIX: explicit real-time vs. Batches routing decision
# ----------------------------------------------------------------------

def choose_processing_strategy(num_documents: int, latency_requirement: str) -> tuple[str, str]:
    """latency_requirement: 'sync_user_waiting' | 'few_minutes' |
    'hours_ok' | 'next_day_ok'"""
    if latency_requirement == "sync_user_waiting":
        return ("real_time_messages_api",
                "a user or downstream system is blocked on this result; "
                "Batches has no speed SLA (up to 24h) so it's disqualified "
                "regardless of volume or cost savings")
    if num_documents >= 20 and latency_requirement in ("hours_ok", "next_day_ok"):
        return ("message_batches_api",
                f"{num_documents} documents with a latency-tolerant "
                f"deadline -- bulk processing where the ~50% cost discount "
                f"is worth the uncertain turnaround, and nothing is "
                f"blocked waiting on it")
    return ("real_time_messages_api",
            "volume too low or deadline too tight to justify Batches' "
            "latency uncertainty")


# ----------------------------------------------------------------------
# Demo
# ----------------------------------------------------------------------

LONG_DOCUMENT_SECTIONS = [
    "PAGE 1 of 3\nACME PROCUREMENT SYSTEM - INVOICE RECORD\n"
    "Invoice #: INV-1029\nDate Issued: 2026-03-14\n"
    "Terms: Net 30. This invoice is subject to the standard purchase "
    "agreement executed between both parties on file with procurement.",

    "PAGE 2 of 3\nLINE ITEMS\n"
    "1) Widget A - qty 10 - $45.00 each\n2) Widget B - qty 3 - $120.00 each\n"
    "Please verify quantities against the original purchase order before "
    "approving payment.",

    "PAGE 3 of 3\nPAYMENT SUMMARY\n"
    "Total Due: $1,240.00\nRemit To: Acme Corp Holdings\n"
    "Please remit payment within terms to avoid late fees.",
]

DOCUMENT_MISSING_VENDOR_SECTIONS = [
    "PAGE 1 of 1\nInvoice #: INV-2200\nDate Issued: 2026-04-02\n"
    "Total Due: $89.50\n(no vendor letterhead or remit-to line present "
    "anywhere in this scanned document)",
]


def _print_outcome(label: str, outcome: ExtractionOutcome) -> None:
    print(f"  -> status={outcome.status}, attempts={outcome.attempts}, data={outcome.data}")


def main() -> None:
    print("=" * 72)
    print("PART 1 -- multi-pass vs. single-pass on a long document")
    print("(expect: single-pass misses fields that only appear past the")
    print(" truncation window; multi-pass catches all of them, Trap #3)")
    print("=" * 72)
    full_text = "\n\n".join(LONG_DOCUMENT_SECTIONS)
    single = process_single_pass(full_text)
    multi = process_multi_pass(LONG_DOCUMENT_SECTIONS)
    print(f"single-pass (truncated at {MAX_SINGLE_PASS_CHARS} chars): {single}")
    print(f"multi-pass (per-section -> integrated):                 {multi}")
    missing_in_single = set(multi) - set(single)
    print(f"fields single-pass silently dropped: {missing_in_single or 'none'}")

    print("\n" + "=" * 72)
    print("PART 2 -- validation retry: specific feedback vs. generic feedback")
    print("(expect: specific feedback converges in one retry; generic")
    print(" feedback never converges, Trap #1)")
    print("=" * 72)
    print("\n[specific feedback]")
    trace_a: list[str] = []
    outcome_a = extract_with_validation_retry(
        "INV-1029", dict(multi), INVOICE_SCHEMA, specific_feedback=True, trace=trace_a
    )
    print("\n".join(trace_a))
    _print_outcome("specific", outcome_a)

    print("\n[generic feedback]")
    trace_b: list[str] = []
    outcome_b = extract_with_validation_retry(
        "INV-1029-generic", dict(multi), INVOICE_SCHEMA, specific_feedback=False, trace=trace_b
    )
    print("\n".join(trace_b))
    _print_outcome("generic", outcome_b)

    print("\n" + "=" * 72)
    print("PART 3 -- persistent, unrecoverable failure -> requires_human_review")
    print("(expect: vendor_name is genuinely absent from the source; no")
    print(" amount of retrying invents it, so it's escalated, not looped forever)")
    print("=" * 72)
    partial = process_multi_pass(DOCUMENT_MISSING_VENDOR_SECTIONS)
    trace_c: list[str] = []
    outcome_c = extract_with_validation_retry(
        "INV-2200", partial, INVOICE_SCHEMA, specific_feedback=True, trace=trace_c
    )
    print("\n".join(trace_c))
    _print_outcome("missing vendor", outcome_c)
    print(f"human review queue size: {len(HUMAN_REVIEW_QUEUE)}")

    print("\n" + "=" * 72)
    print("PART 4 -- real-time vs. Message Batches routing")
    print("(expect: sync-waiting requests NEVER route to Batches, even at")
    print(" high volume, Trap #2)")
    print("=" * 72)
    scenarios = [
        (1, "sync_user_waiting", "single doc, user is on the page waiting for a result"),
        (5000, "next_day_ok", "nightly bulk ingest of yesterday's scanned invoices"),
        (3, "few_minutes", "a handful of docs, small internal tool, no hard deadline"),
        (5000, "sync_user_waiting", "large batch, but a live checkout flow is blocked on it"),
    ]
    for num_docs, latency, description in scenarios:
        strategy, reason = choose_processing_strategy(num_docs, latency)
        print(f"- {description}")
        print(f"    num_docs={num_docs}, latency='{latency}' -> {strategy}")
        print(f"    reason: {reason}")


if __name__ == "__main__":
    main()
