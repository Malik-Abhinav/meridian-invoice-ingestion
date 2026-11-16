"""Deterministic reconciliation tests using controlled visible invoice values."""

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from meridian.evaluation_fixtures import FixtureGroundTruth
from meridian.reconciliation import (
    InMemoryTrustedRepository, PriorInvoiceRecord, ProductRecord,
    PurchaseOrderLineRecord, PurchaseOrderRecord, ReconciliationDecision,
    ReconciliationFindingCode as Code, ReconciliationPolicy, SupplierRecord,
    normalize_invoice_number, reconcile_invoice,
)
from meridian.reconciliation_evaluation import controlled_extraction, fixture_repository
from meridian.validation import ReviewDecision, validate_invoice


ROOT = Path(__file__).resolve().parents[1]
TRUTHS = tuple(FixtureGroundTruth.model_validate_json(path.read_text()) for path in sorted(
    (ROOT / "output/evaluation/ground_truth").glob("*.json")))


def setup(case: str = "fixture_a_clean_01"):
    truth = next(item for item in TRUTHS if item.case_id == case)
    extraction, ocr = controlled_extraction(truth)
    return extraction, validate_invoice(extraction, ocr), fixture_repository(truth, TRUTHS)


def run(extraction, validation, repository, policy=None, **kwargs):
    return reconcile_invoice(extraction, validation, repository, policy, **kwargs)


def codes(result):
    return {finding.code for finding in result.findings}


def repo(base, **changes):
    return InMemoryTrustedRepository(
        suppliers=changes.get("suppliers", base.suppliers()),
        products=changes.get("products", base.products()),
        purchase_orders=changes.get("purchase_orders", base.purchase_orders()),
        prior_invoices=changes.get("prior_invoices", base.prior_invoices()))


def changed_invoice(extraction, **changes):
    return extraction.model_copy(update={"invoice": extraction.invoice.model_copy(update=changes)})


def changed_line(extraction, row=0, **changes):
    lines = list(extraction.invoice.lines)
    lines[row] = lines[row].model_copy(update=changes)
    return changed_invoice(extraction, lines=tuple(lines))


def test_valid_supplier_po_and_no_mutation_or_posting() -> None:
    extraction, validation, trusted = setup()
    before = extraction.model_dump(mode="json")
    one = run(extraction, validation, trusted)
    assert one == run(extraction, validation, trusted)
    assert one.decision == ReconciliationDecision.READY_FOR_POSTING_REVIEW
    assert one.findings == ()
    assert one.inventory_posting_authorized is False
    assert extraction.model_dump(mode="json") == before


@pytest.mark.parametrize(("case", "expected"), [
    ("fixture_a_po_reference_03", Code.UNKNOWN_PO_REFERENCE),
    ("fixture_b_quantity_05", Code.PO_QUANTITY_MISMATCH),
    ("fixture_b_unknown_06", Code.UNKNOWN_PRODUCT),
    ("fixture_c_price_08", Code.PO_PRICE_MISMATCH),
    ("fixture_c_supplier_09", Code.PO_SUPPLIER_MISMATCH),
])
def test_seeded_external_discrepancies(case, expected) -> None:
    result = run(*setup(case))
    assert expected in codes(result)
    assert result.decision == ReconciliationDecision.NEEDS_REVIEW


def test_arithmetic_remains_task12_and_cannot_be_overridden() -> None:
    result = run(*setup("fixture_a_arithmetic_02"))
    assert result.validation_decision == ReviewDecision.NEEDS_REVIEW
    assert result.findings == ()
    assert result.decision == ReconciliationDecision.NEEDS_REVIEW


def test_supplier_unknown_alias_ambiguity_and_missing() -> None:
    extraction, validation, trusted = setup()
    assert Code.UNKNOWN_SUPPLIER in codes(run(extraction, validation, repo(trusted, suppliers=())))
    supplier = trusted.suppliers()[0]
    aliased = supplier.model_copy(update={"aliases": ("Warehouse alias",)})
    changed = changed_invoice(extraction, supplier_name=extraction.invoice.supplier_name.model_copy(
        update={"value": "Warehouse alias"}))
    assert run(changed, validation, repo(trusted, suppliers=(aliased,))).supplier_match.matched_by == "alias"
    second = SupplierRecord(id="second", code="SECOND", name="Different supplier",
                            aliases=(supplier.name,))
    assert Code.AMBIGUOUS_SUPPLIER in codes(run(extraction, validation,
        repo(trusted, suppliers=(supplier, second))))
    missing = changed_invoice(extraction, supplier_name=extraction.invoice.supplier_name.model_copy(
        update={"value": None}))
    assert Code.MISSING_SUPPLIER in codes(run(missing, validation, trusted))


@pytest.mark.parametrize("status", ["closed", "cancelled", "draft"])
def test_ineligible_po_status(status) -> None:
    extraction, validation, trusted = setup()
    po = trusted.purchase_orders()[0].model_copy(update={"status": status})
    assert Code.PO_INELIGIBLE in codes(run(extraction, validation,
        repo(trusted, purchase_orders=(po,))))


def test_missing_and_ambiguous_po_reference() -> None:
    extraction, validation, trusted = setup()
    missing = changed_invoice(extraction, purchase_order_reference=extraction.invoice.purchase_order_reference.model_copy(
        update={"value": None}))
    assert Code.MISSING_PO_REFERENCE in codes(run(missing, validation, trusted))
    duplicate = trusted.purchase_orders()[0].model_copy(update={"id": "second"})
    assert Code.AMBIGUOUS_PO_REFERENCE in codes(run(extraction, validation,
        repo(trusted, purchase_orders=trusted.purchase_orders() + (duplicate,))))


def test_identifier_format_leading_zero_and_ambiguity() -> None:
    extraction, validation, trusted = setup()
    first = extraction.invoice.lines[0]
    assert first.upc.value.startswith("0")
    assert run(extraction, validation, trusted).product_matches[0].product_ids
    malformed = changed_line(extraction, upc=first.upc.model_copy(update={"value": first.upc.value[1:]}))
    assert Code.MALFORMED_PRODUCT_IDENTIFIER in codes(run(malformed, validation, trusted))
    unknown = changed_line(extraction, upc=first.upc.model_copy(update={"value": "000000000000"}))
    assert Code.UNKNOWN_PRODUCT in codes(run(unknown, validation, trusted))
    twin = trusted.products()[0].model_copy(update={"id": "twin"})
    assert Code.AMBIGUOUS_PRODUCT in codes(run(extraction, validation,
        repo(trusted, products=trusted.products() + (twin,))))


def test_ndc_is_exact_and_conflicting_catalog_identifiers_are_not_resolved() -> None:
    extraction, validation, trusted = setup()
    second = extraction.invoice.lines[1]
    assert second.ndc.value.startswith("0")
    assert run(extraction, validation, trusted).product_matches[1].product_ids
    malformed = changed_line(extraction, row=1, ndc=second.ndc.model_copy(
        update={"value": "00000-0102-35"}))
    assert Code.MALFORMED_PRODUCT_IDENTIFIER in codes(run(malformed, validation, trusted))
    first = extraction.invoice.lines[0]
    conflicting = changed_line(extraction, upc=first.upc,
                               ndc=second.ndc)
    assert Code.CONFLICTING_PRODUCT_IDENTIFIERS in codes(run(conflicting, validation, trusted))


def test_missing_identifier_does_not_fabricate_po_mismatch() -> None:
    extraction, validation, trusted = setup()
    first = extraction.invoice.lines[0]
    changed = changed_line(extraction, upc=first.upc.model_copy(update={"value": None}))
    finding_codes = codes(run(changed, validation, trusted))
    assert Code.MISSING_PRODUCT_IDENTIFIER in finding_codes
    assert Code.PRODUCT_NOT_ON_PO not in finding_codes
    assert Code.PO_PRICE_MISMATCH not in finding_codes


def test_product_not_on_po_and_ambiguous_po_line() -> None:
    extraction, validation, trusted = setup()
    po = trusted.purchase_orders()[0]
    absent = po.model_copy(update={"lines": po.lines[1:]})
    assert Code.PRODUCT_NOT_ON_PO in codes(run(extraction, validation,
        repo(trusted, purchase_orders=(absent,))))
    duplicate_line = po.lines[0].model_copy(update={"id": "second-line"})
    ambiguous = po.model_copy(update={"lines": po.lines + (duplicate_line,)})
    assert Code.AMBIGUOUS_PO_LINE in codes(run(extraction, validation,
        repo(trusted, purchase_orders=(ambiguous,))))


def test_repeated_product_rows_aggregate_and_partial_receipts() -> None:
    extraction, validation, trusted = setup()
    po = trusted.purchase_orders()[0]
    first = extraction.invoice.lines[0]
    repeated = first.model_copy(update={"source_row_order": 3,
                                "quantity": first.quantity.model_copy(update={"value": 1})})
    changed = changed_invoice(extraction, lines=extraction.invoice.lines + (repeated,))
    assert Code.PO_QUANTITY_MISMATCH in codes(run(changed, validation, trusted))
    # A smaller invoice is a valid partial receipt; an earlier receipt reduces capacity.
    smaller = changed_line(extraction, quantity=first.quantity.model_copy(update={"value": 1}))
    assert Code.PO_QUANTITY_MISMATCH not in codes(run(smaller, validation, trusted))
    received = po.lines[0].model_copy(update={"received_quantity": 2})
    adjusted = po.model_copy(update={"lines": (received,) + po.lines[1:]})
    assert Code.PO_QUANTITY_MISMATCH in codes(run(smaller, validation,
        repo(trusted, purchase_orders=(adjusted,))))


def test_missing_values_skip_numeric_comparison() -> None:
    extraction, validation, trusted = setup()
    first = extraction.invoice.lines[0]
    changed = changed_line(extraction, quantity=first.quantity.model_copy(update={"value": None}),
                           unit_cost=first.unit_cost.model_copy(update={"value": None}))
    finding_codes = codes(run(changed, validation, trusted))
    assert Code.MISSING_MATCH_INPUT in finding_codes
    assert Code.PO_QUANTITY_MISMATCH not in finding_codes
    assert Code.PO_PRICE_MISMATCH not in finding_codes
    assert "line[1].quantity" in run(changed, validation, trusted).unperformed_checks


def test_duplicate_identity_hash_repeat_conflict_and_supplier_scope() -> None:
    extraction, validation, trusted = setup()
    number = normalize_invoice_number(extraction.invoice.invoice_number.value)
    supplier = trusted.suppliers()[0]
    same = PriorInvoiceRecord(id="previous", supplier_id=supplier.id,
                              invoice_number_normalized=number, source_sha256=extraction.source_sha256)
    exact = codes(run(extraction, validation, repo(trusted, prior_invoices=(same,))))
    assert {Code.DUPLICATE_INVOICE_IDENTITY, Code.EXACT_REPEAT_SUBMISSION} <= exact
    conflicting = same.model_copy(update={"source_sha256": "0" * 64})
    assert Code.CONFLICTING_INVOICE_DOCUMENT in codes(run(extraction, validation,
        repo(trusted, prior_invoices=(conflicting,))))
    other_supplier = same.model_copy(update={"supplier_id": "other"})
    other_codes = codes(run(extraction, validation, repo(trusted, prior_invoices=(other_supplier,))))
    assert Code.DUPLICATE_DOCUMENT_HASH in other_codes
    assert Code.DUPLICATE_INVOICE_IDENTITY not in other_codes
    different_hash = other_supplier.model_copy(update={"source_sha256": "0" * 64})
    assert Code.DUPLICATE_INVOICE_IDENTITY not in codes(run(extraction, validation,
        repo(trusted, prior_invoices=(different_hash,))))


def test_rejected_prior_is_ignored_by_default_and_policy_can_include_it() -> None:
    extraction, validation, trusted = setup()
    previous = PriorInvoiceRecord(id="rejected", supplier_id=trusted.suppliers()[0].id,
                                  invoice_number_normalized=extraction.invoice.invoice_number.value,
                                  source_sha256=extraction.source_sha256, status="rejected")
    with_prior = repo(trusted, prior_invoices=(previous,))
    assert run(extraction, validation, with_prior).decision == ReconciliationDecision.READY_FOR_POSTING_REVIEW
    included = run(extraction, validation, with_prior,
                   ReconciliationPolicy(ignored_prior_statuses=()))
    assert Code.EXACT_REPEAT_SUBMISSION in codes(included)
    failed = previous.model_copy(update={"id": "failed", "status": "failed"})
    assert run(extraction, validation, repo(trusted, prior_invoices=(failed,))).findings == ()


def test_multiple_uploads_one_invoice_report_exact_and_conflicting_without_duplicate_ids() -> None:
    extraction, validation, trusted = setup()
    prior = PriorInvoiceRecord(id="one-invoice", supplier_id=trusted.suppliers()[0].id,
                               invoice_number_normalized=extraction.invoice.invoice_number.value,
                               source_sha256=extraction.source_sha256)
    changed_document = prior.model_copy(update={"source_sha256": "0" * 64})
    result = run(extraction, validation, repo(trusted, prior_invoices=(prior, prior, changed_document)))
    assert {Code.EXACT_REPEAT_SUBMISSION, Code.CONFLICTING_INVOICE_DOCUMENT} <= codes(result)
    assert result.duplicate_check.identity_invoice_ids == ("one-invoice",)
    assert result.duplicate_check.hash_invoice_ids == ("one-invoice",)
    assert Code.DUPLICATE_DOCUMENT_HASH not in codes(result)


def test_current_invoice_or_upload_attempt_can_be_excluded_from_duplicate_read() -> None:
    extraction, validation, trusted = setup()
    current_id, attempt_id = uuid4(), uuid4()
    previous = PriorInvoiceRecord(id=str(current_id), supplier_id=trusted.suppliers()[0].id,
                                  invoice_number_normalized=extraction.invoice.invoice_number.value,
                                  source_sha256=extraction.source_sha256)
    upload = PriorInvoiceRecord(id=f"upload:{attempt_id}", supplier_id=None,
                                invoice_number_normalized=None, source_sha256=extraction.source_sha256,
                                status="upload:pending")
    records = repo(trusted, prior_invoices=(previous, upload))
    assert run(extraction, validation, records,
               current_invoice_id=current_id, current_upload_attempt_id=attempt_id).findings == ()
    assert Code.DUPLICATE_DOCUMENT_HASH in codes(run(extraction, validation, records,
                                                    current_invoice_id=current_id))


def test_receipt_limits_exact_over_full_and_incomplete_history() -> None:
    extraction, validation, trusted = setup()
    po = trusted.purchase_orders()[0]
    first = extraction.invoice.lines[0]
    one = changed_line(extraction, quantity=first.quantity.model_copy(update={"value": 1}))
    def with_receipts(received, complete=True):
        line = po.lines[0].model_copy(update={"received_quantity": received,
                                              "receipt_history_complete": complete})
        return repo(trusted, purchase_orders=(po.model_copy(
            update={"lines": (line,) + po.lines[1:]}),))
    assert Code.PO_QUANTITY_MISMATCH not in codes(run(one, validation, with_receipts(1)))
    assert Code.PO_QUANTITY_MISMATCH in codes(run(extraction, validation, with_receipts(1)))
    assert Code.PO_QUANTITY_MISMATCH in codes(run(one, validation, with_receipts(2)))
    incomplete = run(one, validation, with_receipts(0, False))
    assert Code.INCONSISTENT_RECEIPT_HISTORY in codes(incomplete)
    assert any("remaining_quantity" in item for item in incomplete.unperformed_checks)
    assert incomplete.decision == ReconciliationDecision.NEEDS_REVIEW


def test_trusted_po_line_snapshot_requires_explicit_receipt_state() -> None:
    with pytest.raises(ValidationError):
        PurchaseOrderLineRecord(id="line", product_id="product", ordered_quantity=5,
                                expected_unit_cost=Decimal("1.0000"))


def test_ambiguous_po_lines_with_different_prices_skip_allocation_and_price() -> None:
    extraction, validation, trusted = setup()
    po = trusted.purchase_orders()[0]
    repeated = po.lines[0].model_copy(update={"id": "other-price",
                                               "expected_unit_cost": Decimal("99.0000")})
    ambiguous = repo(trusted, purchase_orders=(po.model_copy(
        update={"lines": po.lines + (repeated,)}),))
    result = run(extraction, validation, ambiguous)
    assert result == run(extraction, validation, ambiguous)
    assert Code.AMBIGUOUS_PO_LINE in codes(result)
    assert Code.PO_PRICE_MISMATCH not in codes(result)
    assert "line[1].unit_cost" in result.unperformed_checks
    assert result.decision == ReconciliationDecision.NEEDS_REVIEW


def test_unreadable_identifier_is_distinct_from_unknown_catalog_value() -> None:
    extraction, validation, trusted = setup()
    first = extraction.invoice.lines[0]
    unreadable = changed_line(extraction, upc=first.upc.model_copy(update={"value": None}))
    result = run(unreadable, validation, trusted)
    assert Code.MISSING_PRODUCT_IDENTIFIER in codes(result)
    assert Code.UNREADABLE_PRODUCT_IDENTIFIER in codes(result)
    assert Code.UNKNOWN_PRODUCT not in codes(result)
    assert "line[1].po_line" in result.unperformed_checks


def test_explicit_rejection_policy_and_source_hash_guard() -> None:
    extraction, validation, trusted = setup("fixture_b_unknown_06")
    assert run(extraction, validation, trusted).decision == ReconciliationDecision.NEEDS_REVIEW
    policy = ReconciliationPolicy(reject_codes=(Code.UNKNOWN_PRODUCT,))
    assert run(extraction, validation, trusted, policy).decision == ReconciliationDecision.REJECTED
    earlier_rejection = validation.model_copy(update={"decision": ReviewDecision.REJECTED})
    assert run(extraction, earlier_rejection, trusted).decision == ReconciliationDecision.REJECTED
    wrong = extraction.model_copy(update={"source_sha256": "0" * 64})
    with pytest.raises(ValueError, match="source hashes differ"):
        run(wrong, validation, trusted)


def test_conservative_invoice_number_normalization() -> None:
    assert normalize_invoice_number(" ab- 01 ") == "AB- 01"
    assert normalize_invoice_number("  ") is None
    assert normalize_invoice_number("AB-01") != normalize_invoice_number("AB 01")


def test_findings_have_stable_row_order_and_reference_values() -> None:
    extraction, validation, trusted = setup("fixture_b_quantity_05")
    one = run(extraction, validation, trusted)
    assert one == run(extraction, validation, trusted)
    assert [(f.source_row_order or 0, f.field, f.code.value, f.reference_ids)
            for f in one.findings] == sorted(
                (f.source_row_order or 0, f.field, f.code.value, f.reference_ids)
                for f in one.findings)
    mismatch = next(f for f in one.findings if f.code == Code.PO_QUANTITY_MISMATCH)
    assert (mismatch.source_row_order, mismatch.observed_value, mismatch.reference_value) == (2, "7", "5")
    assert mismatch.reference_ids and mismatch.manual_review_required
