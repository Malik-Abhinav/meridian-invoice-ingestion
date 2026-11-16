"""Pure, deterministic invoice-local validation of OCR-backed extraction."""

from decimal import Decimal
from enum import StrEnum
import re
from typing import Literal

from pydantic import Field

from meridian.extraction import (
    EvidenceRef, ExtractedInvoice, ExtractionResult, MoneyValue,
)
from meridian.ocr import OcrDocument
from meridian.synthetic import SyntheticModel


class FindingCode(StrEnum):
    MISSING_SUPPLIER = "missing_supplier"
    MISSING_INVOICE_NUMBER = "missing_invoice_number"
    MISSING_INVOICE_DATE = "missing_invoice_date"
    MISSING_PO_REFERENCE = "missing_po_reference"
    UNREADABLE_FIELD = "unreadable_field"
    UNCERTAIN_FIELD = "uncertain_field"
    LOW_OCR_CONFIDENCE = "low_ocr_confidence"
    MISSING_EVIDENCE = "missing_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    MISSING_LINES = "missing_lines"
    MISSING_PRODUCT_IDENTIFIER = "missing_product_identifier"
    MISSING_DESCRIPTION = "missing_description"
    MISSING_QUANTITY = "missing_quantity"
    NONPOSITIVE_QUANTITY = "nonpositive_quantity"
    MISSING_UNIT_COST = "missing_unit_cost"
    NONPOSITIVE_UNIT_COST = "nonpositive_unit_cost"
    MISSING_LINE_TOTAL = "missing_line_total"
    NEGATIVE_LINE_TOTAL = "negative_line_total"
    DUPLICATE_PRODUCT_IDENTIFIER = "duplicate_product_identifier"
    LINE_EXTENSION_MISMATCH = "line_extension_mismatch"
    MISSING_SUBTOTAL = "missing_subtotal"
    MISSING_TAX = "missing_tax"
    MISSING_TOTAL = "missing_total"
    NEGATIVE_SUBTOTAL = "negative_subtotal"
    NEGATIVE_TAX = "negative_tax"
    NEGATIVE_TOTAL = "negative_total"
    SUBTOTAL_MISMATCH = "subtotal_mismatch"
    TOTAL_MISMATCH = "total_mismatch"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ReviewDecision(StrEnum):
    READY_FOR_PO_MATCHING = "ready_for_po_matching"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class FieldReference(SyntheticModel):
    field: str
    source_row_order: int | None = None


class ValidationFinding(SyntheticModel):
    code: FindingCode
    severity: Severity
    explanation: str
    field: FieldReference
    observed_values: dict[str, str | None] = Field(default_factory=dict)
    evidence: tuple[EvidenceRef, ...] = ()
    manual_review_required: bool = True


class ConfidenceAssessment(SyntheticModel):
    """Observable signals only; no calibrated probability or composite score."""

    field: FieldReference
    value_present: bool
    evidence_available: bool
    ocr_confidence_min: Decimal | None
    model_uncertainty: str | None
    evidence_support: Literal["supported", "unsupported", "conflicting", "not_evaluable"]
    requires_review: bool


class ValidationPolicy(SyntheticModel):
    minimum_ocr_confidence: Decimal = Field(default=Decimal("50"), ge=0, le=100)
    prohibit_zero_unit_cost: bool = True
    reject_empty_invoice: bool = False


class ValidationResult(SyntheticModel):
    source_sha256: str
    findings: tuple[ValidationFinding, ...]
    confidence: tuple[ConfidenceAssessment, ...]
    decision: ReviewDecision
    external_reconciliation_evaluated: bool = False


_HEADERS = {
    "supplier_name": FindingCode.MISSING_SUPPLIER,
    "invoice_number": FindingCode.MISSING_INVOICE_NUMBER,
    "invoice_date": FindingCode.MISSING_INVOICE_DATE,
    "purchase_order_reference": FindingCode.MISSING_PO_REFERENCE,
}
_TOTALS = {
    "printed_subtotal": (FindingCode.MISSING_SUBTOTAL, FindingCode.NEGATIVE_SUBTOTAL),
    "printed_tax": (FindingCode.MISSING_TAX, FindingCode.NEGATIVE_TAX),
    "printed_total": (FindingCode.MISSING_TOTAL, FindingCode.NEGATIVE_TOTAL),
}


def _printed(value: object) -> str | None:
    return None if value is None else str(value)


def _numeric_evidence_support(value: object, words: list[str]) -> str:
    if not isinstance(value, (int, Decimal)) or isinstance(value, bool):
        return "not_evaluable"
    tokens = re.findall(r"-?\d+(?:\.\d+)?", " ".join(words).replace(",", ""))
    if not tokens:
        return "not_evaluable"
    try:
        numbers = [Decimal(token) for token in tokens]
    except Exception:
        return "not_evaluable"
    if Decimal(value) not in numbers:
        return "conflicting"
    # A whole-row citation containing several numbers does not identify a column.
    return "supported" if len(set(numbers)) == 1 else "not_evaluable"


def _cited_words(evidence: tuple[EvidenceRef, ...], pages: dict) -> list:
    words = []
    for ref in evidence:
        page = pages.get(ref.page_number)
        if page is None or len(set(ref.word_indexes)) != len(ref.word_indexes):
            raise ValueError("OCR evidence refers to an absent page or repeated word index")
        for index in ref.word_indexes:
            if index < 0 or index >= len(page.words):
                raise ValueError("OCR evidence word index is out of range")
            word = page.words[index]
            if (word.block_number, word.paragraph_number, word.line_number) != (
                    ref.block_number, ref.paragraph_number, ref.line_number):
                raise ValueError("OCR evidence line ID does not match the cited word")
            words.append(word)
    return words


def _review_decision(findings: list[ValidationFinding], *, empty: bool,
                     policy: ValidationPolicy) -> ReviewDecision:
    if empty and policy.reject_empty_invoice:
        return ReviewDecision.REJECTED
    if any(finding.manual_review_required for finding in findings):
        return ReviewDecision.NEEDS_REVIEW
    return ReviewDecision.READY_FOR_PO_MATCHING


def validate_invoice(invoice: ExtractedInvoice | ExtractionResult, document: OcrDocument,
                     policy: ValidationPolicy | None = None) -> ValidationResult:
    """Check only invoice-internal observations; never consult fixture truth or PO data."""
    policy = policy or ValidationPolicy()
    if isinstance(invoice, ExtractionResult):
        if invoice.source_sha256 != document.source_sha256:
            raise ValueError("extraction and OCR document source hashes differ")
        invoice = invoice.invoice
    findings: list[ValidationFinding] = []
    assessments: list[ConfidenceAssessment] = []
    pages = {page.page_number: page for page in document.pages}

    def add(code: FindingCode, severity: Severity, field: FieldReference,
            explanation: str, observed: dict[str, object], evidence=()) -> None:
        findings.append(ValidationFinding(
            code=code, severity=severity, explanation=explanation, field=field,
            observed_values={key: _printed(value) for key, value in observed.items()},
            evidence=tuple(evidence), manual_review_required=severity != Severity.INFO))

    def assess(name: str, wrapped, row: int | None = None, *, critical: bool = True) -> None:
        field = FieldReference(field=name, source_row_order=row)
        words = _cited_words(wrapped.evidence, pages)
        minimum = min((Decimal(str(word.confidence)) for word in words), default=None)
        support = ("unsupported" if wrapped.value is not None and not words else
                   _numeric_evidence_support(wrapped.value, [word.text for word in words])
                   if words and wrapped.value is not None else "not_evaluable")
        review = bool(critical and (wrapped.value is None or not words or
                      wrapped.uncertainty in ("medium", "high") or
                      minimum is not None and minimum < policy.minimum_ocr_confidence or
                      support == "conflicting"))
        assessments.append(ConfidenceAssessment(
            field=field, value_present=wrapped.value is not None,
            evidence_available=bool(words), ocr_confidence_min=minimum,
            model_uncertainty=wrapped.uncertainty, evidence_support=support,
            requires_review=review))
        if wrapped.value is None and words:
            add(FindingCode.UNREADABLE_FIELD, Severity.WARNING, field,
                "OCR evidence exists, but the value was unreadable.", {}, wrapped.evidence)
        if wrapped.value is not None and critical:
            if not words:
                add(FindingCode.MISSING_EVIDENCE, Severity.WARNING, field,
                    "The extracted value has no OCR evidence.", {"value": wrapped.value})
            if wrapped.uncertainty in ("medium", "high"):
                add(FindingCode.UNCERTAIN_FIELD, Severity.WARNING, field,
                    "The extraction provider marked this value uncertain.",
                    {"value": wrapped.value, "uncertainty": wrapped.uncertainty}, wrapped.evidence)
            if minimum is not None and minimum < policy.minimum_ocr_confidence:
                add(FindingCode.LOW_OCR_CONFIDENCE, Severity.WARNING, field,
                    "At least one cited OCR word is below the configured confidence floor.",
                    {"minimum": minimum, "floor": policy.minimum_ocr_confidence}, wrapped.evidence)
            if support == "conflicting":
                add(FindingCode.CONFLICTING_EVIDENCE, Severity.ERROR, field,
                    "The cited numeric OCR words do not contain the extracted value.",
                    {"value": wrapped.value, "ocr_words": " ".join(word.text for word in words)},
                    wrapped.evidence)

    for name, code in _HEADERS.items():
        wrapped = getattr(invoice, name)
        assess(name, wrapped)
        if wrapped.value is None:
            add(code, Severity.ERROR, FieldReference(field=name),
                "Required invoice header is missing.", {}, wrapped.evidence)

    if not invoice.lines:
        add(FindingCode.MISSING_LINES, Severity.ERROR, FieldReference(field="lines"),
            "No invoice line items were extracted.", {})

    seen: dict[tuple[str, str], int] = {}
    for line in sorted(invoice.lines, key=lambda item: item.source_row_order):
        row = line.source_row_order
        for name in ("description", "upc", "ndc", "quantity", "unit_cost", "printed_line_total"):
            wrapped = getattr(line, name)
            assess(name, wrapped, row, critical=(name != "description" and
                   (name not in ("upc", "ndc") or wrapped.value is not None)))
        def ref(name: str) -> FieldReference:
            return FieldReference(field=name, source_row_order=row)
        if line.upc.value is None and line.ndc.value is None:
            add(FindingCode.MISSING_PRODUCT_IDENTIFIER, Severity.ERROR, ref("product_identifier"),
                "Neither UPC nor NDC was extracted.", {}, line.upc.evidence + line.ndc.evidence)
        if line.description.value is None:
            add(FindingCode.MISSING_DESCRIPTION, Severity.WARNING, ref("description"),
                "Line description is missing.", {}, line.description.evidence)
        for name in ("upc", "ndc"):
            wrapped = getattr(line, name)
            if wrapped.value is None:
                continue
            key = (name, wrapped.value.strip())
            if key in seen:
                add(FindingCode.DUPLICATE_PRODUCT_IDENTIFIER, Severity.WARNING, ref(name),
                    "Product identifier repeats on another invoice line.",
                    {"identifier": wrapped.value, "first_row": seen[key]}, wrapped.evidence)
            else:
                seen[key] = row
        for name, code in (("quantity", FindingCode.MISSING_QUANTITY),
                           ("unit_cost", FindingCode.MISSING_UNIT_COST),
                           ("printed_line_total", FindingCode.MISSING_LINE_TOTAL)):
            wrapped = getattr(line, name)
            if wrapped.value is None:
                add(code, Severity.ERROR, ref(name), "Required line value is missing.", {}, wrapped.evidence)
        if line.quantity.value is not None and line.quantity.value <= 0:
            add(FindingCode.NONPOSITIVE_QUANTITY, Severity.ERROR, ref("quantity"),
                "Invoice quantity must be positive.", {"quantity": line.quantity.value}, line.quantity.evidence)
        if line.unit_cost.value is not None and (line.unit_cost.value < 0 or
                policy.prohibit_zero_unit_cost and line.unit_cost.value == 0):
            add(FindingCode.NONPOSITIVE_UNIT_COST, Severity.ERROR, ref("unit_cost"),
                "Unit cost violates the configured nonpositive-cost policy.",
                {"unit_cost": line.unit_cost.value}, line.unit_cost.evidence)
        if line.printed_line_total.value is not None and line.printed_line_total.value < 0:
            add(FindingCode.NEGATIVE_LINE_TOTAL, Severity.ERROR, ref("printed_line_total"),
                "Printed line total is negative.", {"total": line.printed_line_total.value},
                line.printed_line_total.evidence)
        if all(wrapped.value is not None for wrapped in
               (line.quantity, line.unit_cost, line.printed_line_total)):
            expected = line.quantity.value * line.unit_cost.value
            if expected != line.printed_line_total.value:
                add(FindingCode.LINE_EXTENSION_MISMATCH, Severity.ERROR, ref("printed_line_total"),
                    "Quantity times unit cost differs from the printed line total.",
                    {"calculated": expected, "printed": line.printed_line_total.value},
                    line.printed_line_total.evidence)

    for name, (missing_code, negative_code) in _TOTALS.items():
        wrapped: MoneyValue = getattr(invoice, name)
        assess(name, wrapped)
        field = FieldReference(field=name)
        if wrapped.value is None:
            add(missing_code, Severity.ERROR, field, "Printed invoice total is missing.", {}, wrapped.evidence)
        elif wrapped.value < 0:
            add(negative_code, Severity.ERROR, field, "Printed invoice total is negative.",
                {"value": wrapped.value}, wrapped.evidence)

    if invoice.lines and all(line.printed_line_total.value is not None for line in invoice.lines) \
            and invoice.printed_subtotal.value is not None:
        calculated = sum((line.printed_line_total.value for line in invoice.lines), Decimal("0"))
        if calculated != invoice.printed_subtotal.value:
            add(FindingCode.SUBTOTAL_MISMATCH, Severity.ERROR,
                FieldReference(field="printed_subtotal"),
                "Sum of printed line totals differs from printed subtotal.",
                {"calculated": calculated, "printed": invoice.printed_subtotal.value},
                invoice.printed_subtotal.evidence)
    if all(wrapped.value is not None for wrapped in
           (invoice.printed_subtotal, invoice.printed_tax, invoice.printed_total)):
        calculated = invoice.printed_subtotal.value + invoice.printed_tax.value
        if calculated != invoice.printed_total.value:
            add(FindingCode.TOTAL_MISMATCH, Severity.ERROR, FieldReference(field="printed_total"),
                "Printed subtotal plus tax differs from printed total.",
                {"calculated": calculated, "printed": invoice.printed_total.value},
                invoice.printed_total.evidence)

    findings.sort(key=lambda finding: (finding.field.source_row_order or 0,
                                       finding.field.field, finding.code.value))
    assessments.sort(key=lambda item: (item.field.source_row_order or 0, item.field.field))
    decision = _review_decision(findings, empty=not invoice.lines, policy=policy)
    return ValidationResult(source_sha256=document.source_sha256, findings=tuple(findings),
                            confidence=tuple(assessments), decision=decision)
