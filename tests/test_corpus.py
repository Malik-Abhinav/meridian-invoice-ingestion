"""Task 17 corpus provenance, category, and scoring contracts."""

from collections import Counter
import ast
import copy
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re

import pytest

from meridian.corpus import (
    CATEGORIES, MASTER_SEED, CorpusTruth, build_case, validate_manifest,
)
from meridian.corpus_evaluation import (
    _classification, controlled_extraction, controlled_validation, evaluate_corpus,
)
from meridian.pdf_invoices import render_invoice_pdf
from meridian.reconciliation import reconcile_invoice
from meridian.synthetic import CaseCategory, SyntheticInvoiceInput, derive_case_seed


ROOT = Path(__file__).resolve().parents[1] / "output/corpus"


def test_manifest_distribution_and_artifacts():
    manifest = validate_manifest(ROOT)
    assert manifest.case_count == 70
    assert manifest.category_counts == {category.value: 10 for category in CATEGORIES}
    assert sorted(manifest.supplier_counts.values()) == [23, 23, 24]
    assert sorted(manifest.layout_counts.values()) == [23, 23, 24]
    assert len(list((ROOT / "original").glob("*.pdf"))) == 70
    assert len(list((ROOT / "scanned").glob("*.pdf"))) == 70
    referenced = {entry_path for entry in manifest.entries for entry_path in
                  (entry.original_pdf_path, entry.scanned_pdf_path, entry.input_path,
                   entry.ground_truth_path, entry.scan_metadata_path)} | {"manifest.json"}
    actual = {str(path.relative_to(ROOT)) for path in ROOT.rglob("*")
              if path.is_file() and "reports" not in path.parts}
    assert actual == referenced
    assert len(actual) == 351


def test_case_seeds_and_generation_are_stable():
    one = build_case(CaseCategory.CLEAN, 3)
    assert one == build_case(CaseCategory.CLEAN, 3)
    assert one != build_case(CaseCategory.CLEAN, 4)
    assert derive_case_seed(MASTER_SEED, one.generation.case_id) == derive_case_seed(MASTER_SEED, one.generation.case_id)
    assert render_invoice_pdf(one.generation) == render_invoice_pdf(one.generation)


def test_renderer_rejects_evaluation_truth():
    case = build_case(CaseCategory.CLEAN, 0)
    with pytest.raises(TypeError):
        render_invoice_pdf(case.ground_truth)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        render_invoice_pdf(case)  # type: ignore[arg-type]
    from meridian.scanned_invoices import ScanProfile, scan_invoice
    with pytest.raises(TypeError):
        scan_invoice(render_invoice_pdf(case.generation), case.ground_truth,
                     master_seed=MASTER_SEED, profile=ScanProfile.CLEAN)  # type: ignore[arg-type]


def test_production_reconciliation_has_no_evaluation_imports():
    path = Path(__file__).resolve().parents[1] / "src/meridian/reconciliation.py"
    imports = [node.module or "" for node in ast.walk(ast.parse(path.read_text()))
               if isinstance(node, ast.ImportFrom)]
    assert all("corpus" not in module and "evaluation_fixtures" not in module
               for module in imports)


@pytest.mark.parametrize("category", CATEGORIES)
def test_category_business_contract(category):
    manifest = validate_manifest(ROOT)
    entry = next(e for e in manifest.entries if e.category == category)
    invoice = SyntheticInvoiceInput.model_validate_json((ROOT / entry.input_path).read_text())
    truth = CorpusTruth.model_validate_json((ROOT / entry.ground_truth_path).read_text())
    assert truth.case.generation == invoice
    assert sum((line.line_total for line in invoice.lines), Decimal(0)) == truth.case.ground_truth.expected_totals.subtotal
    assert all(line.line_total == line.quantity * line.unit_cost for line in invoice.lines)
    assert truth.case.ground_truth.expected_totals.total == (truth.case.ground_truth.expected_totals.subtotal
                                                              + truth.case.ground_truth.expected_totals.tax)
    assert (invoice.displayed_totals is None) == (category == CaseCategory.MISSING_TOTALS)
    extraction = controlled_extraction(invoice, entry.sha256["scanned_pdf_path"])
    validation = controlled_validation(extraction)
    reconciliation = reconcile_invoice(extraction, validation, truth.repository())
    codes = {f.code.value for f in validation.findings} | {f.code.value for f in reconciliation.findings}
    assert codes == set(truth.expected_validation_codes) | set(truth.expected_reconciliation_codes)
    assert bool(codes) == (reconciliation.decision.value != "ready_for_posting_review")


def test_identifiers_and_nonduplicate_identity():
    manifest = validate_manifest(ROOT)
    identities = Counter()
    identity_cases = {}
    for entry in manifest.entries:
        invoice = SyntheticInvoiceInput.model_validate_json((ROOT / entry.input_path).read_text())
        identities[(invoice.supplier.code, invoice.invoice_number)] += 1
        identity_cases.setdefault((invoice.supplier.code, invoice.invoice_number), []).append(entry)
        assert invoice.invoice_date.year == 2029
        assert all(line.quantity > 0 and line.unit_cost > 0 for line in invoice.lines)
        assert re.fullmatch(r"[0-9A-F]{8}", invoice.invoice_number.split("-INV-")[1])
        assert re.fullmatch(r"[0-9A-F]{8}", invoice.purchase_order_reference.split("-PO-")[1])
        for product in invoice.products:
            if product.upc:
                assert len(product.upc) == 12 and product.upc.startswith("000")
            if product.ndc:
                assert len(product.ndc) == 11 and product.ndc.isascii() and product.ndc.isdecimal()
    assert sum(count > 1 for count in identities.values()) == 9
    assert max(identities.values()) == 3
    for cases in identity_cases.values():
        if len(cases) > 1:
            assert cases[0].category == CaseCategory.CLEAN
            assert all(case.category == CaseCategory.DUPLICATE_INVOICE and
                       case.related_duplicate_case_id == cases[0].case_id for case in cases[1:])


def test_duplicate_provenance_and_hash_policies():
    manifest = validate_manifest(ROOT)
    duplicates = [e for e in manifest.entries if e.category == CaseCategory.DUPLICATE_INVOICE]
    assert len(duplicates) == 10
    for entry in duplicates:
        truth = CorpusTruth.model_validate_json((ROOT / entry.ground_truth_path).read_text())
        assert truth.duplicate_relationship == "distinct_bytes_same_identity"
        assert len(truth.prior_invoices) == 2
        hashes = {p.source_sha256 for p in truth.prior_invoices}
        assert entry.sha256["scanned_pdf_path"] in hashes
        assert len(hashes) == 2
        related = next(e for e in manifest.entries if e.case_id == entry.related_duplicate_case_id)
        assert related.sha256["scanned_pdf_path"] in hashes
        assert manifest.entries.index(related) < manifest.entries.index(entry)


def test_manifest_detects_artifact_tampering(tmp_path):
    manifest = validate_manifest(ROOT)
    entry = manifest.entries[0]
    (tmp_path / "corpus").mkdir()
    target = tmp_path / "corpus"
    (target / "manifest.json").write_bytes((ROOT / "manifest.json").read_bytes())
    (target / entry.original_pdf_path).parent.mkdir()
    (target / entry.original_pdf_path).write_bytes(b"modified")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_manifest(target)
    manifest_data = json.loads((ROOT / "manifest.json").read_text())
    manifest_data["entries"][0]["original_pdf_path"] = "../evaluation/protected.pdf"
    (target / "manifest.json").write_text(json.dumps(manifest_data))
    with pytest.raises(ValueError, match="unsafe artifact path"):
        validate_manifest(target)


def test_ground_truth_is_not_embedded_in_pdf():
    manifest = validate_manifest(ROOT)
    for entry in manifest.entries:
        pdf = (ROOT / entry.original_pdf_path).read_bytes()
        assert b"expected_reconciliation_codes" not in pdf
        assert b"unknown_product_skus" not in pdf
        assert sha256(pdf).hexdigest() == entry.sha256["original_pdf_path"]


def test_classification_reports_false_positives_and_negatives():
    score = _classification({("a", "expected"), ("b", "hit")},
                            {("a", "unexpected"), ("b", "hit")})
    assert (score["true_positives"], score["false_positives"], score["false_negatives"]) == (1, 1, 1)
    assert score["precision"] == score["recall"] == score["f1"] == 0.5
    assert _classification({("a", "expected")}, {("a", "wrong")})["f1"] == 0.0


def test_controlled_scoring_does_not_claim_ocr_accuracy():
    report = evaluate_corpus(ROOT, run_ocr=False)
    assert report["ocr_mock"]["ocr"] is None
    assert report["controlled"]["groups"]["overall"]["findings"]["false_negatives"] == 0
    assert report["controlled"]["groups"]["overall"]["unperformed_check_count"] > 0
    assert report["duplicate_detection"]["controlled"] == {
        "duplicate_invoice_identity": 10, "exact_repeat_submission": 10,
        "conflicting_invoice_document": 10, "duplicate_document_hash": 0}
    assert report["primary_defects"]["controlled"] == {
        "expected_cases": 50, "detected_cases": 50}
    assert report["controlled"]["groups"]["overall"]["validation_findings"]["true_positives"] == 30
    assert report["controlled"]["groups"]["overall"]["reconciliation_findings"]["true_positives"] == 60


def test_expected_findings_do_not_depend_on_observed_oracle(monkeypatch):
    import meridian.corpus_evaluation as evaluation
    from meridian.validation import ReviewDecision, ValidationResult

    def suppress_local(extraction):
        return ValidationResult(source_sha256=extraction.source_sha256,
                                findings=(), confidence=(), decision=ReviewDecision.READY_FOR_PO_MATCHING)

    monkeypatch.setattr(evaluation, "controlled_validation", suppress_local)
    report = evaluation.evaluate_corpus(ROOT, run_ocr=False)
    assert report["controlled"]["groups"]["overall"]["validation_findings"]["false_negatives"] == 30
    assert report["controlled"]["groups"]["overall"]["findings"]["false_negatives"] == 30
    assert all(row["expected_validation_findings"] == ["missing_subtotal", "missing_tax", "missing_total"]
               for row in report["controlled"]["rows"] if row["category"] == "missing_totals")


def test_ocr_and_mock_run_before_truth_is_loaded(monkeypatch):
    import inspect
    import meridian.corpus_evaluation as evaluation
    calls = {"ocr": 0, "mock": 0}

    def observed_ocr(pdf):
        assert isinstance(pdf, bytes) and pdf.startswith(b"%PDF-")
        assert "truth" not in inspect.currentframe().f_back.f_locals
        calls["ocr"] += 1
        return object()

    def observed_mock(document, provider):
        assert "truth" not in inspect.currentframe().f_back.f_locals
        calls["mock"] += 1
        raise RuntimeError("deliberate mock stop")

    monkeypatch.setattr(evaluation, "extract_ocr", observed_ocr)
    monkeypatch.setattr(evaluation, "extract_invoice", observed_mock)
    report = evaluation.evaluate_corpus(ROOT)
    assert calls == {"ocr": 70, "mock": 70}
    assert report["ocr_mock"]["processing_failures"] == 70


@pytest.mark.parametrize("failure_stage", ("mock", "validation"))
def test_ocr_denominator_survives_downstream_failures(monkeypatch, failure_stage):
    from types import SimpleNamespace
    import meridian.corpus_evaluation as evaluation
    from meridian.ocr_evaluation import InvoiceOcrEvaluation

    monkeypatch.setattr(evaluation, "extract_ocr", lambda pdf: object())
    monkeypatch.setattr(evaluation, "evaluate_invoice", lambda case_id, *_: InvoiceOcrEvaluation(
        case_id=case_id, status="ok", character_error_rate=0.0, word_error_rate=0.0,
        character_edits=0, reference_characters=1, word_edits=0, reference_words=1,
        token_multiset_error_rate=0.0, observed_tokens=1, processing_seconds=0.0))
    if failure_stage == "mock":
        def failed_mock(*_):
            raise RuntimeError("mock failed")
        monkeypatch.setattr(evaluation, "extract_invoice", failed_mock)
    else:
        monkeypatch.setattr(evaluation, "extract_invoice",
                            lambda *_: SimpleNamespace(invoice=evaluation._empty_invoice()))
        def failed_validation(*_):
            raise RuntimeError("validation failed")
        monkeypatch.setattr(evaluation, "validate_invoice", failed_validation)
    report = evaluation.evaluate_corpus(ROOT)
    assert len(report["ocr_mock"]["ocr"]["invoices"]) == 70
    assert len(report["ocr_mock"]["extraction"]["invoices"]) == 70
    assert report["ocr_mock"]["processing_failures"] == 70
    assert report["ocr_mock"]["ocr_failures"] == []
    from meridian.corpus_report_audit import audit_report
    assert audit_report(report)["ocr_reference_characters"] == 70


def test_persisted_report_aggregates_recompute_independently():
    from meridian.corpus_report_audit import audit_report
    report = json.loads((ROOT / "reports/metrics.json").read_text())
    audited = audit_report(report)
    assert audited["cases"] == 70
    assert audited["finding_pairs"]["true_positives"] == 90
    assert report["ocr_mock"]["extraction"]["fields"]["product_identifier"]["correct"] == 29
    tampered = copy.deepcopy(report)
    tampered["ocr_mock"]["groups"]["overall"]["findings"]["true_positives"] += 1
    with pytest.raises(ValueError, match="finding counts"):
        audit_report(tampered)
    tampered = copy.deepcopy(report)
    tampered["ocr_mock"]["ocr"]["character_error_rate"] += 0.01
    with pytest.raises(ValueError, match="character_error_rate"):
        audit_report(tampered)
    tampered = copy.deepcopy(report)
    tampered["ocr_mock"]["extraction"]["fields"]["quantity"]["expected"] += 1
    with pytest.raises(ValueError, match="extraction denominator"):
        audit_report(tampered)


def test_generation_same_and_different_seed(tmp_path):
    from meridian.corpus import generate_corpus
    roots = [tmp_path / name / "corpus" for name in ("first", "second", "third")]
    for root in roots[:2]:
        generate_corpus(root, master_seed=MASTER_SEED)
    generate_corpus(roots[2], master_seed=MASTER_SEED + 1)
    def hashes(root):
        return {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
                for path in root.rglob("*") if path.is_file()}
    first, second, third = (hashes(root) for root in roots)
    assert first == second
    assert set(first) == set(third)
    assert first != third
    assert first["manifest.json"] != third["manifest.json"]
    assert first["original/corpus_clean_01.pdf"] != third["original/corpus_clean_01.pdf"]


def test_failure_analysis_has_no_trailing_whitespace(tmp_path):
    from meridian.corpus_evaluation import write_report
    report = evaluate_corpus(ROOT, run_ocr=False)
    report["ocr_mock"]["rows"] = [{**report["controlled"]["rows"][0],
                                    "false_positives": ["missing_lines"]}]
    report["representative_cases"][0]["ocr_mock"] = {
        **report["ocr_mock"]["rows"][0], "ocr_text": "x " * 201}
    write_report(tmp_path, report)
    assert all(line == line.rstrip() for line in
               (tmp_path / "reports/analysis.md").read_text().splitlines())


def test_generator_restricts_output_to_corpus(tmp_path):
    from meridian.corpus import generate_corpus
    with pytest.raises(ValueError, match="corpus"):
        generate_corpus(tmp_path / "evaluation")
    with pytest.raises(ValueError, match="separate"):
        generate_corpus(tmp_path / "evaluation" / "corpus")


def test_missing_and_incorrect_extraction_denominators():
    from meridian.extraction_evaluation import _score
    missing = _score(None, "printed")
    incorrect = _score("misread", "printed")
    omitted = _score(None, None)
    assert (missing.expected, missing.missing, missing.unsupported) == (1, 1, 0)
    assert (incorrect.expected, incorrect.missing, incorrect.unsupported) == (1, 0, 1)
    assert (omitted.expected, omitted.missing, omitted.unsupported) == (0, 0, 0)
