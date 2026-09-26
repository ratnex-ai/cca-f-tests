"""
04 - STRUCTURED OUTPUT, VALIDATION RETRY, BATCHES  (CCA-F Domain 4.3-4.5)
=============================================================================
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 04_structured_output.py
=============================================================================
"""

import json
import os
import time

import anthropic

client = anthropic.Anthropic()

# ---------------------------------------------------------------------------
# The extraction tool. Its input_schema IS the output schema you want back.
# Reading tool_use.input eliminates JSON SYNTAX errors entirely.
# It does NOT eliminate SEMANTIC errors - hence the validator further down.
# ---------------------------------------------------------------------------
EXTRACT_INVOICE = {
    "name": "extract_invoice",
    "description": (
        "Record the fields found in an invoice document. Call this exactly "
        "once per document. Report values as they appear in the source; do "
        "not compute, infer, or fill in values that are absent."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            # NULLABLE, not required. A required field the document lacks is
            # an invitation to fabricate. This is the fix for hallucinated
            # values - not a lower temperature.
            "vendor": {"type": ["string", "null"]},
            "invoice_number": {"type": ["string", "null"]},
            "issued_on": {
                "type": ["string", "null"],
                "description": "Normalise to YYYY-MM-DD regardless of the "
                               "source format (3/4/26, 4 Mar 2026, etc.)",
            },
            # Emitting BOTH lets you detect the mismatch a schema cannot.
            "stated_total_cents": {"type": ["integer", "null"]},
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "amount_cents": {"type": "integer"},
                    },
                    "required": ["description", "amount_cents"],
                },
            },
            # enum + "other" + a detail field = extensible categories.
            # "unclear" gives the model somewhere to put genuine ambiguity
            # instead of guessing.
            "doc_type": {
                "type": "string",
                "enum": ["invoice", "receipt", "credit_note", "other", "unclear"],
            },
            "doc_type_detail": {"type": ["string", "null"]},
            # Lets the model flag internally inconsistent SOURCE data rather
            # than quietly picking one value.
            "conflict_detected": {"type": "boolean"},
            "conflict_note": {"type": ["string", "null"]},
        },
        "required": ["doc_type", "line_items", "conflict_detected"],
    },
}

FEW_SHOT = """Here is how to handle formats you will encounter.

<example>
Document: "Invoice #A-77 ... Services rendered 1,250.00 ... Materials 300.00
... TOTAL DUE 1,550.00"
Extraction: line_items = [{"description":"Services rendered",
"amount_cents":125000},{"description":"Materials","amount_cents":30000}],
stated_total_cents = 155000, conflict_detected = false
Reasoning: amounts are decimal currency, so multiply by 100 for minor units.
</example>

<example>
Document: "Statement of account ... balance carried forward ... no itemisation"
Extraction: doc_type = "unclear", doc_type_detail = "account statement, not
an invoice", line_items = [], stated_total_cents = null
Reasoning: the field is genuinely absent. Emit null. Do not infer a total
from surrounding text.
</example>

<example>
Document: "Subtotal 900.00 ... Total 900.00 ... (footer) Amount payable 990.00"
Extraction: stated_total_cents = 90000, conflict_detected = true,
conflict_note = "footer states 990.00, body states 900.00"
Reasoning: the SOURCE disagrees with itself. Flag it; do not choose.
</example>"""


def extract(document_text: str, correction: str | None = None) -> dict:
    """One extraction call. `correction` carries validation feedback on retry."""
    content = FEW_SHOT + "\n\nDocument:\n" + document_text
    if correction:
        content += (
            "\n\nYour previous extraction failed validation:\n" + correction
            + "\n\nRe-extract. Re-read the document for items you may have "
              "missed. If the information is genuinely not in the document, "
              "emit null and set conflict_detected appropriately."
        )

    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=4096,
        tools=[EXTRACT_INVOICE],
        # FORCED tool choice: the model must call this specific tool. Compare:
        #   {"type": "auto"} -> it may reply with prose instead
        #   {"type": "any"}  -> it must call A tool, but picks which. Use this
        #                       when you have several schemas and the document
        #                       type is unknown.
        tool_choice={"type": "tool", "name": "extract_invoice"},
        messages=[{"role": "user", "content": content}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input       # already a dict, schema-conformant
    raise RuntimeError("no tool_use block returned")


def validate(extraction: dict) -> list[str]:
    """SEMANTIC validation. None of this is expressible in JSON Schema."""
    errors = []
    computed = sum(i["amount_cents"] for i in extraction["line_items"])
    stated = extraction.get("stated_total_cents")
    if stated is not None and extraction["line_items"] and computed != stated:
        errors.append(
            f"line items sum to {computed} but stated_total_cents is {stated}"
        )
    if extraction["doc_type"] == "other" and not extraction.get("doc_type_detail"):
        errors.append("doc_type is 'other' but doc_type_detail is empty")
    if extraction.get("conflict_detected") and not extraction.get("conflict_note"):
        errors.append("conflict_detected is true but conflict_note is empty")
    return errors


def extract_with_retry(document_text: str, max_attempts: int = 3) -> dict:
    """Retry WITH ERROR FEEDBACK. Retrying an unchanged request is pointless -
    the same input produces the same output. What changes here is the prompt."""
    correction = None
    for attempt in range(1, max_attempts + 1):
        result = extract(document_text, correction)
        errors = validate(result)
        if not errors:
            return result
        print(f"  attempt {attempt} failed: {errors}")
        correction = "; ".join(errors)

    # Give up honestly rather than returning a wrong answer. If the required
    # information simply is not in the source document, no number of retries
    # will produce it - that case belongs in human review, not a retry loop.
    result["_validation_failed"] = validate(result)
    result["_needs_human_review"] = True
    return result


# ---------------------------------------------------------------------------
# BATCH API - 50% cheaper, up to a 24h window, no latency SLA.
# Right for overnight audits. Wrong for a blocking pre-merge check.
# Also: no multi-turn tool calling inside one batch request - you cannot run a
# tool mid-request and feed the result back. Single-shot extraction is fine.
# ---------------------------------------------------------------------------
def batch_extract(documents: dict[str, str]) -> dict:
    requests = [
        {
            # custom_id is how you correlate a response back to its input.
            # Without it you cannot tell which document failed.
            "custom_id": doc_id,
            "params": {
                "model": "claude-opus-5",
                "max_tokens": 4096,
                "tools": [EXTRACT_INVOICE],
                "tool_choice": {"type": "tool", "name": "extract_invoice"},
                "messages": [{"role": "user",
                              "content": FEW_SHOT + "\n\nDocument:\n" + text}],
            },
        }
        for doc_id, text in documents.items()
    ]

    batch = client.messages.batches.create(requests=requests)
    print(f"submitted batch {batch.id} with {len(requests)} requests")

    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        time.sleep(30)

    extractions, failures = {}, []
    for entry in client.messages.batches.results(batch.id):
        if entry.result.type != "succeeded":
            # Resubmit ONLY these, with a modification (e.g. chunk a document
            # that blew the context limit). Resubmitting the whole batch
            # unchanged wastes the 50% saving you came for.
            failures.append(entry.custom_id)
            continue
        for block in entry.result.message.content:
            if block.type == "tool_use":
                extractions[entry.custom_id] = block.input

    return {"extractions": extractions, "failed_custom_ids": failures}


# ---------------------------------------------------------------------------
# INDEPENDENT REVIEW (Domain 4.6). A fresh client call with NO generation
# history is what makes this work - not a "please double-check" instruction,
# and not extended thinking. The generator already talked itself into its
# answer; the reviewer has never seen that reasoning.
# ---------------------------------------------------------------------------
def independent_review(document_text: str, extraction: dict) -> dict:
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=2048,
        system=(
            "You are verifying an extraction produced by a different system. "
            "You have no knowledge of how it was produced. For each field, "
            "state agree/disagree and a confidence from 0 to 1. Output only "
            "JSON: {\"fields\": [{\"name\":..., \"verdict\":..., "
            "\"confidence\":..., \"note\":...}]}"
        ),
        messages=[{
            "role": "user",
            "content": (f"Document:\n{document_text}\n\n"
                        f"Claimed extraction:\n{json.dumps(extraction, indent=2)}")
        }],
    )
    return json.loads("".join(b.text for b in response.content if b.type == "text"))


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("set ANTHROPIC_API_KEY first")

    doc = """ACME SUPPLY CO
    Invoice 2026-0417        Issued 4 March 2026
    Consulting, February          1,250.00
    Travel reimbursement            300.00
    TOTAL DUE                     1,550.00
    (remittance slip)  Pay: 1,505.00"""

    result = extract_with_retry(doc)
    print(json.dumps(result, indent=2))
    print(json.dumps(independent_review(doc, result), indent=2))