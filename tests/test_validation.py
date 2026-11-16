"""Controlled invoice-local validation tests, independent of mock OCR quality."""

from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest

from meridian.extraction import (
    CountValue, DateValue, EvidenceRef, ExtractedInvoice, ExtractedLine, ExtractionResult,
    MoneyValue, TextValue,
)
from meridian.ocr import OcrDocument, OcrPage, OcrWord, PreprocessingConfig
from meridian.validation import (
    FieldReference, FindingCode, ReviewDecision, Severity, ValidationFinding,
    ValidationPolicy, _review_decision, validate_invoice,
)


def sample() -> tuple[ExtractedInvoice, OcrDocument]:
    words: list[OcrWord] = []

    def evidence(text: str, confidence: float = 99) -> tuple[EvidenceRef, ...]:
        index = len(words)
        words.append(OcrWord(text=text, confidence=confidence, left=0, top=index * 10,
                             width=50, height=10, block_number=1, paragraph_number=1,
                             line_number=index + 1, word_number=1))
        return (EvidenceRef(page_number=1, word_indexes=(index,), block_number=1,
                            paragraph_number=1, line_number=index + 1),)

    line = ExtractedLine(
        source_row_order=1, line_number=CountValue(),
        description=TextValue(value="Fictional product", evidence=evidence("Fictional product")),
        upc=TextValue(), ndc=TextValue(value="12345678901", evidence=evidence("12345678901")),
        quantity=CountValue(value=2, evidence=evidence("2")),
        unit_cost=MoneyValue(value=Decimal("1.2500"), evidence=evidence("1.2500")),
        printed_line_total=MoneyValue(value=Decimal("2.5000"), evidence=evidence("2.5000")))
    invoice = ExtractedInvoice(
        supplier_name=TextValue(value="Fictional supplier", evidence=evidence("Fictional supplier")),
        invoice_number=TextValue(value="FICT-001", evidence=evidence("FICT-001")),
        invoice_date=DateValue(value=date(2026, 1, 1), evidence=evidence("2026-01-01")),
        purchase_order_reference=TextValue(value="FICT-PO", evidence=evidence("FICT-PO")),
        lines=(line,),
        printed_subtotal=MoneyValue(value=Decimal("2.5000"), evidence=evidence("2.5000")),
        printed_tax=MoneyValue(value=Decimal("0.0000"), evidence=evidence("0.0000")),
        printed_total=MoneyValue(value=Decimal("2.5000"), evidence=evidence("2.5000")))
    document = OcrDocument(
        source_sha256="a" * 64, engine_version="test", configuration=PreprocessingConfig(),
        pages=(OcrPage(page_number=1, text=" ".join(word.text for word in words),
                       words=tuple(words), image_width=100, image_height=1000,
                       deskew_angle_degrees=0),), normalized_text=" ".join(word.text for word in words))
    return invoice, document


def codes(invoice: ExtractedInvoice, document: OcrDocument, **policy) -> set[FindingCode]:
    return {finding.code for finding in validate_invoice(
        invoice, document, ValidationPolicy(**policy)).findings}


def replace_line(invoice: ExtractedInvoice, **changes) -> ExtractedInvoice:
    return invoice.model_copy(update={"lines": (invoice.lines[0].model_copy(update=changes),)})


def test_clean_is_only_ready_for_po_matching_and_deterministic() -> None:
    invoice, document = sample()
    before = invoice.model_dump(mode="json")
    one = validate_invoice(invoice, document)
    assert one == validate_invoice(invoice, document)
    assert one.decision == ReviewDecision.READY_FOR_PO_MATCHING
    assert one.external_reconciliation_evaluated is False
    assert one.findings == ()
    assert invoice.model_dump(mode="json") == before
    assert all(item.value_present for item in one.confidence if item.field.field not in ("upc",))


def test_noncritical_low_ocr_does_not_block() -> None:
    invoice, document = sample()
    ref = invoice.lines[0].description.evidence[0]
    page = document.pages[0]
    words = list(page.words)
    words[ref.word_indexes[0]] = words[ref.word_indexes[0]].model_copy(update={"confidence": 1})
    changed = document.model_copy(update={"pages": (page.model_copy(update={"words": tuple(words)}),)})
    result = validate_invoice(invoice, changed)
    assert result.decision == ReviewDecision.READY_FOR_PO_MATCHING
    assert not any(finding.code == FindingCode.LOW_OCR_CONFIDENCE for finding in result.findings)


def test_multiple_evidence_references_do_not_prove_numeric_column() -> None:
    invoice, document = sample()
    quantity = invoice.lines[0].quantity
    other = invoice.lines[0].unit_cost.evidence
    changed = replace_line(invoice, quantity=quantity.model_copy(
        update={"evidence": quantity.evidence + other}))
    result = validate_invoice(changed, document)
    assessment = next(item for item in result.confidence if item.field.field == "quantity")
    assert assessment.evidence_support == "not_evaluable"
    assert FindingCode.CONFLICTING_EVIDENCE not in {finding.code for finding in result.findings}


def test_invalid_evidence_location_and_source_hash_are_rejected() -> None:
    invoice, document = sample()
    invalid_ref = invoice.invoice_number.evidence[0].model_copy(update={"line_number": 999})
    invalid = invoice.model_copy(update={"invoice_number": invoice.invoice_number.model_copy(
        update={"evidence": (invalid_ref,)})})
    with pytest.raises(ValueError, match="line ID"):
        validate_invoice(invalid, document)
    source = ExtractionResult(source_sha256="b" * 64, provider="test", model_name="test",
                              repair_attempted=False, invoice=invoice)
    with pytest.raises(ValueError, match="hashes differ"):
        validate_invoice(source, document)


def test_informational_findings_do_not_block() -> None:
    info = ValidationFinding(code=FindingCode.DUPLICATE_PRODUCT_IDENTIFIER,
                             severity=Severity.INFO, explanation="Informational only",
                             field=FieldReference(field="ndc", source_row_order=2),
                             manual_review_required=False)
    assert _review_decision([info], empty=False, policy=ValidationPolicy()) == ReviewDecision.READY_FOR_PO_MATCHING


@pytest.mark.parametrize("name,code", [
    ("supplier_name", FindingCode.MISSING_SUPPLIER),
    ("invoice_number", FindingCode.MISSING_INVOICE_NUMBER),
    ("invoice_date", FindingCode.MISSING_INVOICE_DATE),
    ("purchase_order_reference", FindingCode.MISSING_PO_REFERENCE),
])
def test_missing_and_unreadable_headers(name, code) -> None:
    invoice, document = sample()
    old = getattr(invoice, name)
    missing = invoice.model_copy(update={name: type(old)()})
    assert code in codes(missing, document)
    unreadable = invoice.model_copy(update={name: type(old)(value=None,
                                uncertainty="high", evidence=old.evidence)})
    assert {code, FindingCode.UNREADABLE_FIELD} <= codes(unreadable, document)


def test_uncertainty_low_ocr_and_missing_evidence() -> None:
    invoice, document = sample()
    uncertain = invoice.model_copy(update={"invoice_number": invoice.invoice_number.model_copy(
        update={"uncertainty": "high"})})
    assert FindingCode.UNCERTAIN_FIELD in codes(uncertain, document)
    unsupported = invoice.model_copy(update={"invoice_number": invoice.invoice_number.model_copy(
        update={"evidence": ()})})
    assert FindingCode.MISSING_EVIDENCE in codes(unsupported, document)
    ref = invoice.invoice_number.evidence[0]
    page = document.pages[0]
    words = list(page.words)
    words[ref.word_indexes[0]] = words[ref.word_indexes[0]].model_copy(update={"confidence": 49})
    low = document.model_copy(update={"pages": (page.model_copy(update={"words": tuple(words)}),)})
    assert FindingCode.LOW_OCR_CONFIDENCE in codes(invoice, low)
    assert FindingCode.LOW_OCR_CONFIDENCE not in codes(invoice, low, minimum_ocr_confidence=Decimal("49"))


@pytest.mark.parametrize("value", [0, -2])
def test_nonpositive_quantity(value) -> None:
    invoice, document = sample()
    changed = replace_line(invoice, quantity=invoice.lines[0].quantity.model_copy(update={"value": value}))
    assert FindingCode.NONPOSITIVE_QUANTITY in codes(changed, document)


def test_missing_lines_and_line_information() -> None:
    invoice, document = sample()
    assert FindingCode.MISSING_LINES in codes(invoice.model_copy(update={"lines": ()}), document)
    empty = invoice.lines[0].model_copy(update={
        "ndc": TextValue(), "quantity": CountValue(), "unit_cost": MoneyValue(),
        "printed_line_total": MoneyValue()})
    found = codes(invoice.model_copy(update={"lines": (empty,)}), document)
    assert {FindingCode.MISSING_PRODUCT_IDENTIFIER, FindingCode.MISSING_QUANTITY,
            FindingCode.MISSING_UNIT_COST, FindingCode.MISSING_LINE_TOTAL} <= found
    assert validate_invoice(invoice.model_copy(update={"lines": ()}), document,
                            ValidationPolicy(reject_empty_invoice=True)).decision == ReviewDecision.REJECTED


@pytest.mark.parametrize("amount,code", [
    ("0", FindingCode.NONPOSITIVE_UNIT_COST),
    ("-1.0000", FindingCode.NONPOSITIVE_UNIT_COST),
])
def test_nonpositive_unit_cost(amount, code) -> None:
    invoice, document = sample()
    changed = replace_line(invoice, unit_cost=invoice.lines[0].unit_cost.model_copy(
        update={"value": Decimal(amount)}))
    assert code in codes(changed, document)
    if amount == "0":
        assert code not in codes(changed, document, prohibit_zero_unit_cost=False)


def test_negative_totals_and_exact_arithmetic() -> None:
    invoice, document = sample()
    changed = invoice.model_copy(update={
        "printed_subtotal": invoice.printed_subtotal.model_copy(update={"value": Decimal("-2.5000")}),
        "printed_tax": invoice.printed_tax.model_copy(update={"value": Decimal("-0.0001")}),
        "printed_total": invoice.printed_total.model_copy(update={"value": Decimal("-2.5001")})})
    found = codes(changed, document)
    assert {FindingCode.NEGATIVE_SUBTOTAL, FindingCode.NEGATIVE_TAX,
            FindingCode.NEGATIVE_TOTAL, FindingCode.SUBTOTAL_MISMATCH} <= found
    assert FindingCode.TOTAL_MISMATCH not in found
    one_unit = invoice.model_copy(update={"printed_total": invoice.printed_total.model_copy(
        update={"value": Decimal("2.5001")})})
    assert FindingCode.TOTAL_MISMATCH in codes(one_unit, document)


def test_negative_line_total_and_missing_description() -> None:
    invoice, document = sample()
    changed = replace_line(invoice,
        description=TextValue(),
        printed_line_total=invoice.lines[0].printed_line_total.model_copy(
            update={"value": Decimal("-2.5000")}))
    assert {FindingCode.NEGATIVE_LINE_TOTAL, FindingCode.MISSING_DESCRIPTION,
            FindingCode.LINE_EXTENSION_MISMATCH} <= codes(changed, document)


def test_line_extension_subtotal_duplicate_and_stable_order() -> None:
    invoice, document = sample()
    line = invoice.lines[0]
    second = line.model_copy(update={"source_row_order": 2,
                              "printed_line_total": line.printed_line_total.model_copy(
                                  update={"value": Decimal("2.0000")})})
    changed = invoice.model_copy(update={"lines": (second, line)})
    result = validate_invoice(changed, document)
    found = {finding.code for finding in result.findings}
    assert {FindingCode.DUPLICATE_PRODUCT_IDENTIFIER, FindingCode.LINE_EXTENSION_MISMATCH,
            FindingCode.SUBTOTAL_MISMATCH} <= found
    assert result == validate_invoice(changed, document)
    assert list(result.findings) == sorted(result.findings, key=lambda finding: (
        finding.field.source_row_order or 0, finding.field.field, finding.code.value))


def test_numeric_conflicting_evidence_and_missing_totals() -> None:
    invoice, document = sample()
    changed = replace_line(invoice, quantity=invoice.lines[0].quantity.model_copy(
        update={"value": 3}))
    assert FindingCode.CONFLICTING_EVIDENCE in codes(changed, document)
    missing = invoice.model_copy(update={"printed_subtotal": MoneyValue(),
                                 "printed_tax": MoneyValue(), "printed_total": MoneyValue()})
    assert {FindingCode.MISSING_SUBTOTAL, FindingCode.MISSING_TAX,
            FindingCode.MISSING_TOTAL} <= codes(missing, document)
    assert not {FindingCode.SUBTOTAL_MISMATCH, FindingCode.TOTAL_MISMATCH} & codes(missing, document)
    missing_line = replace_line(invoice, printed_line_total=MoneyValue())
    assert FindingCode.SUBTOTAL_MISMATCH not in codes(missing_line, document)
    missing_cost = replace_line(invoice, unit_cost=MoneyValue())
    assert FindingCode.LINE_EXTENSION_MISMATCH not in codes(missing_cost, document)


def test_all_nine_stored_fixtures_and_arithmetic_discrepancy() -> None:
    root = Path(__file__).resolve().parents[1] / "output"
    manifest = json.loads((root / "evaluation/manifest.json").read_text())
    assert len(manifest["entries"]) == 9
    results = {}
    for entry in manifest["entries"]:
        case = entry["case_id"]
        extracted = ExtractionResult.model_validate_json(
            (root / "extraction" / f"{case}.extraction.json").read_text())
        document = OcrDocument.model_validate_json((root / "ocr" / f"{case}.ocr.json").read_text())
        assert extracted.source_sha256 == document.source_sha256
        results[case] = validate_invoice(extracted, document)
        assert results[case].external_reconciliation_evaluated is False
    assert FindingCode.SUBTOTAL_MISMATCH in {
        finding.code for finding in results["fixture_a_arithmetic_02"].findings}
    assert results["fixture_a_arithmetic_02"].decision == ReviewDecision.NEEDS_REVIEW


def test_clean_fixture_ocr_merge_is_not_a_validator_error() -> None:
    root = Path(__file__).resolve().parents[1] / "output"
    extracted = ExtractionResult.model_validate_json(
        (root / "extraction/fixture_a_clean_01.extraction.json").read_text())
    document = OcrDocument.model_validate_json(
        (root / "ocr/fixture_a_clean_01.ocr.json").read_text())
    second = extracted.invoice.lines[1]
    assert second.source_row_order == 2
    assert (second.quantity.value, second.unit_cost.value, second.printed_line_total.value) == (
        53, Decimal("7.5000"), Decimal("22.5000"))
    assert "bundle 53 7.5000 22.5000" in document.normalized_text
    finding = next(finding for finding in validate_invoice(extracted, document).findings
                   if finding.code == FindingCode.LINE_EXTENSION_MISMATCH)
    assert finding.field.source_row_order == 2
    assert finding.observed_values == {"calculated": "397.5000", "printed": "22.5000"}
