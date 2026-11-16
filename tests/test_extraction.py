"""Structured extraction stays uncertain, exact, and tied to OCR evidence."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from meridian.evaluation_fixtures import FixtureGroundTruth
from meridian.extraction import (
    CountValue, DateValue, ExtractedInvoice, MoneyValue, ProviderError, ResponseError,
    extract_invoice, ocr_payload, validate_evidence,
)
from meridian.extraction_evaluation import evaluate_extraction
from meridian.extraction_providers import MockProvider, OpenAIProvider, response_schema
from meridian.ocr import OcrDocument


ROOT = Path(__file__).resolve().parents[1] / "output"


def ocr(case: str = "fixture_a_clean_01") -> OcrDocument:
    return OcrDocument.model_validate_json((ROOT / "ocr" / f"{case}.ocr.json").read_text())


def test_null_fields_and_duplicate_or_missing_lines_are_representable() -> None:
    null = {"value": None, "uncertainty": "high", "evidence": []}
    invoice = ExtractedInvoice.model_validate_json(json.dumps({
        "supplier_name": null, "invoice_number": null, "invoice_date": null,
        "purchase_order_reference": null, "lines": [],
        "printed_subtotal": null, "printed_tax": null, "printed_total": null,
    }))
    assert invoice.lines == ()
    assert invoice.invoice_number.value is None
    sample = extract_invoice(ocr(), MockProvider()).invoice.lines[0]
    duplicate = invoice.model_copy(update={"lines": (sample, sample.model_copy(update={
        "source_row_order": 2}))})
    assert len(duplicate.lines) == 2
    with pytest.raises(ValidationError):
        ExtractedInvoice.model_validate_json(json.dumps({
            **invoice.model_dump(mode="json"),
            "lines": [sample.model_dump(mode="json"), sample.model_dump(mode="json")],
        }))
    assert CountValue(value=0).value == 0
    assert CountValue(value=-2).value == -2
    assert sample.printed_line_total.value != Decimal("0")


@pytest.mark.parametrize("raw", [4.25, 1, float("nan"), float("inf"), "NaN", "Infinity",
                                  "1.12345", "-1.12345", "9999999999999999999",
                                  "-9999999999999999999", "-Infinity", "oops"])
def test_money_rejects_imprecise_or_invalid_values(raw) -> None:
    with pytest.raises((ValueError, ValidationError)):
        MoneyValue(value=raw)


def test_money_keeps_decimal_exact_and_json_strings() -> None:
    assert MoneyValue.model_validate_json('{"value":"1.2300"}').value == Decimal("1.2300")
    assert MoneyValue.model_validate_json('{"value":"1.2300"}').value.as_tuple().exponent == -4
    assert MoneyValue.model_validate_json('{"value":"-1.2300"}').value == Decimal("-1.2300")
    with pytest.raises(ValidationError):
        MoneyValue.model_validate_json('{"value":1.23}')


def test_offline_mock_is_deterministic_and_uses_only_ocr() -> None:
    document = ocr()
    provider = MockProvider()
    one = extract_invoice(document, provider)
    two = extract_invoice(document, provider)
    assert one == two
    assert one.invoice.invoice_number.value == "FICT_A-INV-0001"
    assert one.invoice.lines
    assert "expected_po" not in ocr_payload(document)
    assert "expected_processing_decision" not in ocr_payload(document)


def test_evidence_refs_are_validated_and_confidence_stays_in_ocr() -> None:
    document = ocr()
    provider = MockProvider()
    result = extract_invoice(document, provider)
    ref = result.invoice.invoice_number.evidence[0]
    word = document.pages[ref.page_number - 1].words[ref.word_indexes[0]]
    assert 0 <= word.confidence <= 100
    data = json.loads(provider.complete(ocr_payload(document)))
    data["invoice_number"]["evidence"][0]["word_indexes"] = [999999]
    with pytest.raises(ResponseError):
        extract_invoice(document, SequenceProvider([json.dumps(data), json.dumps(data)]))
    for field, replacement in (("page_number", 99), ("line_number", 999)):
        bad = json.loads(provider.complete(ocr_payload(document)))
        bad["invoice_number"]["evidence"][0][field] = replacement
        with pytest.raises(ResponseError):
            extract_invoice(document, SequenceProvider([json.dumps(bad), json.dumps(bad)]))


def test_unreadable_null_can_cite_real_ocr_words() -> None:
    document = ocr()
    invoice = extract_invoice(document, MockProvider()).invoice
    ref = invoice.invoice_date.evidence[0]
    unreadable = invoice.model_copy(update={"invoice_date": DateValue(
        value=None, uncertainty="high", evidence=(ref,))})
    validate_evidence(unreadable, document)
    bad_ref = ref.model_copy(update={"page_number": 999})
    invalid = unreadable.model_copy(update={"invoice_date": DateValue(
        value=None, uncertainty="high", evidence=(bad_ref,))})
    with pytest.raises(ResponseError):
        validate_evidence(invalid, document)


def test_repeated_ocr_words_use_distinct_page_word_indexes() -> None:
    document = ocr()
    payload = json.loads(ocr_payload(document))
    words = [word for line in payload["pages"][0]["lines"] for word in line["words"]]
    indexes = [word["index"] for word in words]
    assert len(indexes) == len(set(indexes)) == len(document.pages[0].words)


class SequenceProvider:
    name = "test"
    model_name = "sequence"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def complete(self, ocr_input, repair_output=None):
        self.calls.append((ocr_input, repair_output))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def test_malformed_json_repairs_once_without_fabrication() -> None:
    document = ocr()
    good = MockProvider().complete(ocr_payload(document))
    provider = SequenceProvider(["{bad", good])
    result = extract_invoice(document, provider)
    assert result.repair_attempted
    assert len(provider.calls) == 2 and provider.calls[1][1] == "{bad"
    with pytest.raises(ResponseError):
        extract_invoice(document, SequenceProvider(["{bad", "still bad"]))


def test_provider_error_retry_is_bounded() -> None:
    document = ocr()
    good = MockProvider().complete(ocr_payload(document))
    provider = SequenceProvider([ProviderError("temporary", retryable=True), good])
    assert not extract_invoice(document, provider, max_retries=1).repair_attempted
    assert len(provider.calls) == 2
    with pytest.raises(ProviderError):
        extract_invoice(document, SequenceProvider([ProviderError("down")]), max_retries=0)
    fatal = SequenceProvider([ProviderError("bad key"), good])
    with pytest.raises(ProviderError, match="bad key"):
        extract_invoice(document, fatal)
    assert len(fatal.calls) == 1


def test_unknown_schema_fields_and_numeric_money_rejected() -> None:
    document = ocr()
    data = json.loads(MockProvider().complete(ocr_payload(document)))
    data["unexpected"] = "x"
    with pytest.raises(ResponseError):
        extract_invoice(document, SequenceProvider([json.dumps(data), json.dumps(data)]))
    del data["unexpected"]
    data["printed_total"]["value"] = 31.25
    with pytest.raises(ResponseError):
        extract_invoice(document, SequenceProvider([json.dumps(data), json.dumps(data)]))


def test_arithmetic_error_is_preserved_as_printed() -> None:
    document = ocr("fixture_a_arithmetic_02")
    result = extract_invoice(document, MockProvider())
    truth = FixtureGroundTruth.model_validate_json(
        (ROOT / "evaluation/ground_truth/fixture_a_arithmetic_02.json").read_text())
    assert result.invoice.printed_subtotal.value == truth.displayed_invoice.displayed_totals.subtotal
    assert result.invoice.printed_subtotal.value != truth.expected_correct_totals.subtotal
    assert result.invoice.printed_total.value == truth.displayed_invoice.displayed_totals.total
    assert result.invoice.printed_total.value != truth.expected_correct_totals.total
    assert evaluate_extraction(result.invoice, truth).fields["printed_subtotal"].correct == 1


def test_unknown_identifier_is_kept_as_printed() -> None:
    document = ocr("fixture_b_unknown_06")
    result = extract_invoice(document, MockProvider())
    truth = FixtureGroundTruth.model_validate_json(
        (ROOT / "evaluation/ground_truth/fixture_b_unknown_06.json").read_text())
    printed = truth.displayed_invoice.products[1].ndc
    assert printed not in truth.known_product_identifiers
    assert result.invoice.lines[1].ndc.value == printed
    assert evaluate_extraction(result.invoice, truth).fields["product_identifier"].correct == 2


def test_ocr_error_is_not_silently_corrected() -> None:
    result = extract_invoice(ocr("fixture_c_price_08"), MockProvider())
    assert result.invoice.invoice_number.value is None
    assert result.invoice.lines == ()


def test_openai_provider_needs_environment_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        OpenAIProvider().complete("{}")
    schema = response_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
