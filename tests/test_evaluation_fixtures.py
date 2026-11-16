"""Checks for the nine evaluation artifacts, independent of application validation."""

from collections import Counter
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from pydantic import ValidationError
from pypdf import PdfReader

from meridian.evaluation_fixtures import (
    ExpectedFinding,
    FixtureGroundTruth,
    FixtureManifest,
    FixtureScenario,
    ProcessingDecision,
    build_fixture_cases,
    generate_fixture_set,
)
from meridian.scanned_invoices import ScanMetadata, ScanProfile
from meridian.synthetic import InvoiceTotals, PrintedTotals, SyntheticInvoiceInput


def _observed_findings(truth: FixtureGroundTruth) -> set[ExpectedFinding]:
    """Evaluate fixture evidence directly; this is a test oracle, not a service."""
    invoice = truth.displayed_invoice
    findings = set()
    if invoice.displayed_totals.subtotal != sum(
        (line.line_total for line in invoice.lines), Decimal("0")
    ):
        findings.add(ExpectedFinding.SUBTOTAL_MISMATCH)
    if invoice.purchase_order_reference != truth.expected_po_reference:
        findings.add(ExpectedFinding.PO_REFERENCE_MISMATCH)
    if invoice.supplier.code != truth.expected_po_supplier_code:
        findings.add(ExpectedFinding.PO_SUPPLIER_MISMATCH)
    po_lines = {line.line_number: line for line in truth.expected_po_lines}
    products = {product.sku: product for product in invoice.products}
    for line in invoice.lines:
        product = products[line.product_sku]
        identifier = product.upc or product.ndc
        if identifier not in truth.known_product_identifiers:
            findings.add(ExpectedFinding.UNKNOWN_PRODUCT)
        if line.quantity != po_lines[line.line_number].quantity:
            findings.add(ExpectedFinding.PO_QUANTITY_MISMATCH)
        if line.unit_cost != po_lines[line.line_number].unit_cost:
            findings.add(ExpectedFinding.PO_PRICE_MISMATCH)
    return findings


def test_nine_cases_cover_each_supplier_scenario_and_scan_profile() -> None:
    cases = build_fixture_cases()
    assert len(cases) == len({case.invoice.case_id for case in cases}) == 9
    assert Counter(case.invoice.supplier.code for case in cases) == {
        "FICT_A": 3, "FICT_B": 3, "FICT_C": 3,
    }
    for supplier in ("FICT_A", "FICT_B", "FICT_C"):
        supplier_cases = [case for case in cases if case.invoice.supplier.code == supplier]
        assert sum(case.truth.scenario == FixtureScenario.CLEAN for case in supplier_cases) == 1
        assert sum(case.truth.scenario in {FixtureScenario.ARITHMETIC, FixtureScenario.QUANTITY,
                                            FixtureScenario.PRICE} for case in supplier_cases) == 1
        assert sum(case.truth.scenario in {FixtureScenario.PO_REFERENCE,
                                            FixtureScenario.UNKNOWN_PRODUCT,
                                            FixtureScenario.PO_SUPPLIER} for case in supplier_cases) == 1
    assert set(ScanProfile).issubset({case.profile for case in cases})
    assert len({case.invoice.invoice_number for case in cases}) == 9
    assert len({case.invoice.invoice_date for case in cases}) == 9
    assert len({case.invoice.purchase_order_reference for case in cases}) == 9
    assert {len(case.invoice.lines) for case in cases} == {2, 3, 4}
    assert all(case.invoice.supplier.fictional for case in cases)
    identifiers = [product.upc or product.ndc for case in cases for product in case.invoice.products]
    assert len(identifiers) == len(set(identifiers))


@pytest.mark.parametrize("case", build_fixture_cases(), ids=lambda case: case.invoice.case_id)
def test_each_case_evidence_and_outcome(case) -> None:
    invoice, truth = case.invoice, case.truth
    assert SyntheticInvoiceInput.model_validate_json(invoice.model_dump_json()) == invoice
    assert FixtureGroundTruth.model_validate_json(truth.model_dump_json()) == truth
    observed = _observed_findings(truth)
    assert observed == set(truth.expected_validation_outcomes)
    assert set(discrepancy.finding for discrepancy in truth.seeded_discrepancies) == observed
    assert truth.expected_correct_totals.subtotal == sum(
        (line.line_total for line in invoice.lines), Decimal("0")
    )
    assert truth.expected_processing_decision == (
        ProcessingDecision.REVIEW_REQUIRED if observed else ProcessingDecision.AUTO_POST_CANDIDATE
    )
    assert bool(observed) == (truth.scenario != FixtureScenario.CLEAN)
    for discrepancy in truth.seeded_discrepancies:
        assert discrepancy.displayed_value != discrepancy.expected_value


def test_arithmetic_error_is_printed_and_original_money_input_remains_strict() -> None:
    invoice = build_fixture_cases()[1].invoice
    assert isinstance(invoice.displayed_totals, PrintedTotals)
    assert invoice.displayed_totals.subtotal - sum(
        (line.line_total for line in invoice.lines), Decimal("0")
    ) == Decimal("1.0000")
    with pytest.raises(ValidationError):
        InvoiceTotals(subtotal=1.0, tax=Decimal("0"), total=Decimal("1"))
    with pytest.raises(ValidationError):
        InvoiceTotals(subtotal="1.0000", tax=Decimal("0"), total=Decimal("1"))
    with pytest.raises(ValidationError):
        InvoiceTotals.model_validate_json('{"subtotal": 1.0, "tax": "0", "total": "1"}')


def test_artifact_manifest_and_document_boundaries(tmp_path: Path) -> None:
    manifest = generate_fixture_set(tmp_path, dpi=96)
    assert FixtureManifest.model_validate_json((tmp_path / "manifest.json").read_text()) == manifest
    assert len(manifest.entries) == 9
    assert all(not entry.related_case_ids for entry in manifest.entries)
    for entry in manifest.entries:
        original_path = tmp_path / entry.original_pdf_path
        scanned_path = tmp_path / entry.scanned_pdf_path
        truth_path = tmp_path / entry.ground_truth_path
        metadata_path = tmp_path / entry.scan_metadata_path
        assert all(path.is_file() for path in (original_path, scanned_path, truth_path, metadata_path))
        truth = FixtureGroundTruth.model_validate_json(truth_path.read_text())
        metadata = ScanMetadata.model_validate_json(metadata_path.read_text())
        original = original_path.read_bytes()
        scanned = scanned_path.read_bytes()
        original_pages = PdfReader(BytesIO(original)).pages
        original_text = "\n".join(page.extract_text() for page in original_pages)
        scan_pages = PdfReader(BytesIO(scanned)).pages
        assert entry.case_id == truth.case_id == metadata.source_case_id
        assert entry.supplier_code == truth.displayed_invoice.supplier.code
        assert entry.invoice_number in original_text
        assert entry.purchase_order_reference in original_text
        for product in truth.displayed_invoice.products:
            assert product.description in original_text
            assert (product.upc or product.ndc) in original_text
        for line in truth.displayed_invoice.lines:
            assert f"{line.line_total:.4f}" in original_text
        assert f"{truth.displayed_invoice.displayed_totals.total:.4f}" in original_text
        assert entry.degradation_profile == metadata.degradation_type
        assert entry.expected_validation_outcomes == truth.expected_validation_outcomes
        assert entry.expected_processing_decision == truth.expected_processing_decision
        assert metadata.source_pdf_sha256 == sha256(original).hexdigest()
        assert metadata.scanned_pdf_sha256 == sha256(scanned).hexdigest()
        assert len(scan_pages) == len(original_pages)
        assert all(page.width_px >= page.source_width_px and
                   page.height_px >= page.source_height_px for page in metadata.pages)
        assert all(not page.extract_text().strip() for page in scan_pages)
        assert all("/XObject" in page["/Resources"] for page in scan_pages)
        assert entry.scenario.value not in original_text
        for discrepancy in truth.seeded_discrepancies:
            if discrepancy.field in {"purchase_order_reference", "products[2].ndc", "supplier.code"}:
                assert discrepancy.expected_value not in original_text


def test_byte_identical_regeneration(tmp_path: Path) -> None:
    generate_fixture_set(tmp_path, dpi=96)
    first = {path.relative_to(tmp_path): sha256(path.read_bytes()).hexdigest()
             for path in tmp_path.rglob("*") if path.is_file()}
    assert len(first) == 37  # 18 PDFs, 18 sidecars/truth records, one manifest.
    generate_fixture_set(tmp_path, dpi=96)
    second = {path.relative_to(tmp_path): sha256(path.read_bytes()).hexdigest()
              for path in tmp_path.rglob("*") if path.is_file()}
    assert first == second
    assert build_fixture_cases(2029) == build_fixture_cases(2029)
    assert build_fixture_cases(2030)[0].invoice.products != build_fixture_cases(2029)[0].invoice.products
