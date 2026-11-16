"""Offline CLI, provider boundary, and fixture evaluation."""

import json
from pathlib import Path

from meridian.extraction import extract_invoice
from meridian.extraction_cli import main
from meridian.extraction_evaluation import aggregate_extractions, evaluate_extraction
from meridian.extraction_providers import MockProvider, OpenAIProvider
from meridian.evaluation_fixtures import FixtureGroundTruth
from meridian.ocr import OcrDocument


ROOT = Path(__file__).resolve().parents[1] / "output"


def test_single_cli_without_api_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    target = tmp_path / "extraction.json"
    assert main(["--input", str(ROOT / "ocr/fixture_a_clean_01.ocr.json"),
                 "--output", str(target)]) == 0
    assert json.loads(target.read_text())["provider"] == "mock"


def test_batch_cli_and_evaluation_do_not_change_ground_truth(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    manifest_path = ROOT / "evaluation/manifest.json"
    before = {p.name: p.read_bytes() for p in (ROOT / "evaluation/ground_truth").glob("*.json")}
    assert main(["--fixtures", str(manifest_path), "--ocr-dir", str(ROOT / "ocr"),
                 "--output-dir", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert not report["failures"]
    assert len(report["evaluation"]["invoices"]) == 9
    assert len(list(tmp_path.glob("*.extraction.json"))) == 9
    assert before == {p.name: p.read_bytes() for p in (ROOT / "evaluation/ground_truth").glob("*.json")}


def test_evaluation_uses_displayed_values_and_counts_missing_ocr() -> None:
    case = "fixture_c_price_08"
    doc = OcrDocument.model_validate_json((ROOT / "ocr" / f"{case}.ocr.json").read_text())
    truth = FixtureGroundTruth.model_validate_json(
        (ROOT / "evaluation/ground_truth" / f"{case}.json").read_text())
    result = extract_invoice(doc, MockProvider())
    evaluation = evaluate_extraction(result.invoice, truth)
    assert evaluation.fields["invoice_number"].missing == 1
    assert evaluation.fields["unit_cost"].missing == 2
    assert evaluation.expected_lines == 2
    assert aggregate_extractions((evaluation, evaluation)).expected_lines == 4


def test_printed_line_numbers_only_apply_to_purchase_order_layout() -> None:
    for case in ("fixture_a_clean_01", "fixture_b_clean_04", "fixture_c_clean_07"):
        doc = OcrDocument.model_validate_json((ROOT / "ocr" / f"{case}.ocr.json").read_text())
        truth = FixtureGroundTruth.model_validate_json(
            (ROOT / "evaluation/ground_truth" / f"{case}.json").read_text())
        result = extract_invoice(doc, MockProvider())
        assert [line.source_row_order for line in result.invoice.lines] == list(
            range(1, len(result.invoice.lines) + 1))
        score = evaluate_extraction(result.invoice, truth)
        if case.startswith("fixture_c"):
            assert score.fields["line_number"].expected == 4
            assert score.matched_lines == 4
        else:
            assert score.fields["line_number"].expected == 0


def test_description_and_identifier_override_wrong_printed_number() -> None:
    case = "fixture_c_clean_07"
    doc = OcrDocument.model_validate_json((ROOT / "ocr" / f"{case}.ocr.json").read_text())
    truth = FixtureGroundTruth.model_validate_json(
        (ROOT / "evaluation/ground_truth" / f"{case}.json").read_text())
    invoice = extract_invoice(doc, MockProvider()).invoice
    first = invoice.lines[0]
    wrong_number = first.line_number.model_copy(update={"value": 1})
    changed = invoice.model_copy(update={"lines": (
        first.model_copy(update={"line_number": wrong_number}), *invoice.lines[1:])})
    score = evaluate_extraction(changed, truth)
    assert score.matched_lines == 4
    assert score.fields["line_number"].unsupported == 1
    assert score.unmatched_line_numbers == ()
    assert score.extra_source_row_orders == ()


def test_openai_request_contains_only_ocr_and_uses_structured_schema(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    sent = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, *_):
            return json.dumps({"status": "completed", "output": [{"content": [
                {"type": "output_text", "text": "{}"}]}]}).encode()

    def fake_urlopen(request, timeout):
        sent.update(json.loads(request.data))
        assert timeout == 7
        assert request.get_header("Authorization") == "Bearer test-key"
        return FakeResponse()

    monkeypatch.setattr("meridian.extraction_providers.urlopen", fake_urlopen)
    assert OpenAIProvider("gpt-4o-mini", timeout_seconds=7).complete('{"pages":[]}') == "{}"
    assert sent["store"] is False
    assert sent["text"]["format"]["strict"] is True
    assert sent["model"] == "gpt-4o-mini"
    assert "expected_po" not in json.dumps(sent)
    assert "untrusted data" in sent["instructions"]
