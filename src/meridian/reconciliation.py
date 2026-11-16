"""Read-only, deterministic comparison with trusted purchasing records."""

from collections import defaultdict
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field

from meridian.extraction import ExtractionResult
from meridian.synthetic import SyntheticModel
from meridian.validation import ReviewDecision, Severity, ValidationResult


class ReconciliationFindingCode(StrEnum):
    MISSING_SUPPLIER = "missing_supplier"
    UNKNOWN_SUPPLIER = "unknown_supplier"
    AMBIGUOUS_SUPPLIER = "ambiguous_supplier"
    MISSING_PO_REFERENCE = "missing_po_reference"
    UNKNOWN_PO_REFERENCE = "unknown_po_reference"
    AMBIGUOUS_PO_REFERENCE = "ambiguous_po_reference"
    PO_SUPPLIER_MISMATCH = "po_supplier_mismatch"
    PO_INELIGIBLE = "po_ineligible"
    MISSING_PRODUCT_IDENTIFIER = "missing_product_identifier"
    UNREADABLE_PRODUCT_IDENTIFIER = "unreadable_product_identifier"
    MALFORMED_PRODUCT_IDENTIFIER = "malformed_product_identifier"
    UNKNOWN_PRODUCT = "unknown_product"
    AMBIGUOUS_PRODUCT = "ambiguous_product"
    CONFLICTING_PRODUCT_IDENTIFIERS = "conflicting_product_identifiers"
    PRODUCT_NOT_ON_PO = "product_not_on_po"
    AMBIGUOUS_PO_LINE = "ambiguous_po_line"
    PO_QUANTITY_MISMATCH = "po_quantity_mismatch"
    PO_PRICE_MISMATCH = "po_price_mismatch"
    MISSING_MATCH_INPUT = "missing_match_input"
    INCONSISTENT_RECEIPT_HISTORY = "inconsistent_receipt_history"
    DUPLICATE_INVOICE_IDENTITY = "duplicate_invoice_identity"
    DUPLICATE_DOCUMENT_HASH = "duplicate_document_hash"
    EXACT_REPEAT_SUBMISSION = "exact_repeat_submission"
    CONFLICTING_INVOICE_DOCUMENT = "conflicting_invoice_document"


class ReconciliationDecision(StrEnum):
    READY_FOR_POSTING_REVIEW = "ready_for_posting_review"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class ReconciliationPolicy(SyntheticModel):
    eligible_po_statuses: tuple[str, ...] = ("open",)
    ignored_prior_statuses: tuple[str, ...] = ("failed", "rejected")
    # Rejection is opt-in and limited to explicit finding codes.
    reject_codes: tuple[ReconciliationFindingCode, ...] = ()


class SupplierRecord(SyntheticModel):
    id: str
    code: str
    name: str
    aliases: tuple[str, ...] = ()


class ProductRecord(SyntheticModel):
    id: str
    name: str
    upc: str | None = None
    ndc: str | None = None


class PurchaseOrderLineRecord(SyntheticModel):
    id: str
    product_id: str
    ordered_quantity: int
    expected_unit_cost: Decimal
    received_quantity: int = Field(ge=0)
    receipt_history_complete: bool


class PurchaseOrderRecord(SyntheticModel):
    id: str
    order_number: str
    supplier_id: str
    status: str
    lines: tuple[PurchaseOrderLineRecord, ...]


class PriorInvoiceRecord(SyntheticModel):
    id: str
    supplier_id: str | None
    invoice_number_normalized: str | None
    source_sha256: str | None = None
    status: str = "validated"


class TrustedRepository(Protocol):
    def suppliers(self) -> tuple[SupplierRecord, ...]: ...
    def products(self) -> tuple[ProductRecord, ...]: ...
    def purchase_orders(self) -> tuple[PurchaseOrderRecord, ...]: ...
    def prior_invoices(self) -> tuple[PriorInvoiceRecord, ...]: ...


class InMemoryTrustedRepository:
    """Frozen reference snapshot for tests and offline evaluation."""

    def __init__(self, *, suppliers: tuple[SupplierRecord, ...] = (),
                 products: tuple[ProductRecord, ...] = (),
                 purchase_orders: tuple[PurchaseOrderRecord, ...] = (),
                 prior_invoices: tuple[PriorInvoiceRecord, ...] = ()) -> None:
        self._suppliers = tuple(suppliers)
        self._products = tuple(products)
        self._purchase_orders = tuple(purchase_orders)
        self._prior_invoices = tuple(prior_invoices)

    def suppliers(self) -> tuple[SupplierRecord, ...]:
        return self._suppliers

    def products(self) -> tuple[ProductRecord, ...]:
        return self._products

    def purchase_orders(self) -> tuple[PurchaseOrderRecord, ...]:
        return self._purchase_orders

    def prior_invoices(self) -> tuple[PriorInvoiceRecord, ...]:
        return self._prior_invoices


class ReconciliationFinding(SyntheticModel):
    code: ReconciliationFindingCode
    severity: Severity
    field: str
    source_row_order: int | None = None
    observed_value: str | None = None
    reference_value: str | None = None
    reference_ids: tuple[str, ...] = ()
    explanation: str
    manual_review_required: bool = True


class SupplierMatch(SyntheticModel):
    observed_name: str | None
    supplier_ids: tuple[str, ...]
    matched_by: str | None = None


class ProductMatch(SyntheticModel):
    source_row_order: int
    product_ids: tuple[str, ...]
    po_line_ids: tuple[str, ...] = ()


class PurchaseOrderMatch(SyntheticModel):
    observed_reference: str | None
    purchase_order_ids: tuple[str, ...]


class DuplicateCheckResult(SyntheticModel):
    normalized_invoice_number: str | None
    identity_invoice_ids: tuple[str, ...]
    hash_invoice_ids: tuple[str, ...]


class ReconciliationResult(SyntheticModel):
    source_sha256: str
    findings: tuple[ReconciliationFinding, ...]
    supplier_match: SupplierMatch
    product_matches: tuple[ProductMatch, ...]
    purchase_order_match: PurchaseOrderMatch
    duplicate_check: DuplicateCheckResult
    unperformed_checks: tuple[str, ...] = ()
    validation_decision: ReviewDecision
    decision: ReconciliationDecision
    inventory_posting_authorized: Literal[False] = False


def normalize_invoice_number(value: str | None) -> str | None:
    """Trim outer whitespace and fold ASCII case; preserve punctuation/inner spaces."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed.translate(str.maketrans("abcdefghijklmnopqrstuvwxyz",
                                          "ABCDEFGHIJKLMNOPQRSTUVWXYZ")) if trimmed else None


def _name(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _valid_identifier(kind: str, value: str) -> bool:
    if not value.isascii() or not value.isdecimal():
        return False
    if kind == "ndc":
        return len(value) == 11  # Only explicitly normalized 5-4-2 form.
    if len(value) != 12:
        return False
    weighted = sum(int(digit) * (3 if index % 2 == 0 else 1)
                   for index, digit in enumerate(value[:11]))
    return (10 - weighted % 10) % 10 == int(value[-1])


def reconcile_invoice(extraction: ExtractionResult,
                      validation: ValidationResult, repository: TrustedRepository,
                      policy: ReconciliationPolicy | None = None,
                      *, current_invoice_id: str | UUID | None = None,
                      current_upload_attempt_id: str | UUID | None = None) -> ReconciliationResult:
    """Never writes to a repository or treats a local validation result as posting permission."""
    policy = policy or ReconciliationPolicy()
    invoice = extraction.invoice
    source_hash = extraction.source_sha256
    if source_hash != validation.source_sha256:
        raise ValueError("extraction and validation source hashes differ")
    findings: list[ReconciliationFinding] = []
    unperformed: set[str] = set()

    def add(code: ReconciliationFindingCode, field: str, explanation: str,
            *, row: int | None = None, observed: object = None, reference: object = None,
            ids: tuple[str, ...] = (), severity: Severity = Severity.ERROR) -> None:
        findings.append(ReconciliationFinding(
            code=code, severity=severity, field=field, source_row_order=row,
            observed_value=None if observed is None else str(observed),
            reference_value=None if reference is None else str(reference),
            reference_ids=tuple(sorted(ids)), explanation=explanation))

    suppliers = sorted(repository.suppliers(), key=lambda item: item.id)
    products = sorted(repository.products(), key=lambda item: item.id)
    orders = sorted(repository.purchase_orders(), key=lambda item: item.id)
    excluded_invoice_id = str(current_invoice_id) if current_invoice_id is not None else None
    excluded_upload_id = (f"upload:{current_upload_attempt_id}"
                          if current_upload_attempt_id is not None else None)
    prior = sorted((item for item in repository.prior_invoices()
                    if item.status not in policy.ignored_prior_statuses
                    and item.id != excluded_invoice_id
                    and item.id != excluded_upload_id),
                   key=lambda item: (item.id, item.source_sha256 or ""))
    observed_supplier = invoice.supplier_name.value
    supplier_candidates: list[SupplierRecord] = []
    matched_by = None
    if observed_supplier is None:
        add(ReconciliationFindingCode.MISSING_SUPPLIER, "supplier_name", "Supplier name is missing.")
    else:
        key = _name(observed_supplier)
        names = [item for item in suppliers if _name(item.name) == key]
        aliases = [item for item in suppliers if key in {_name(a) for a in item.aliases}]
        supplier_candidates = [item for item in suppliers if item in names or item in aliases]
        matched_by = "name" if names else "alias" if aliases else None
        if not supplier_candidates:
            add(ReconciliationFindingCode.UNKNOWN_SUPPLIER, "supplier_name", "No trusted supplier has this name or alias.", observed=observed_supplier)
        elif len(supplier_candidates) > 1:
            add(ReconciliationFindingCode.AMBIGUOUS_SUPPLIER, "supplier_name", "Name or alias identifies multiple suppliers.", observed=observed_supplier, ids=tuple(s.id for s in supplier_candidates))
    supplier_id = supplier_candidates[0].id if len(supplier_candidates) == 1 else None
    if supplier_id is None:
        unperformed.update(("supplier_po_match", "duplicate_identity"))
    supplier_match = SupplierMatch(observed_name=observed_supplier,
                                   supplier_ids=tuple(s.id for s in supplier_candidates), matched_by=matched_by)

    po_ref = invoice.purchase_order_reference.value
    po_candidates = [item for item in orders if item.order_number == po_ref] if po_ref else []
    if not po_ref:
        add(ReconciliationFindingCode.MISSING_PO_REFERENCE, "purchase_order_reference", "Purchase order reference is missing.")
    elif not po_candidates:
        add(ReconciliationFindingCode.UNKNOWN_PO_REFERENCE, "purchase_order_reference", "No trusted purchase order has this reference.", observed=po_ref)
    elif len(po_candidates) > 1:
        add(ReconciliationFindingCode.AMBIGUOUS_PO_REFERENCE, "purchase_order_reference", "Reference identifies multiple purchase orders.", observed=po_ref, ids=tuple(p.id for p in po_candidates))
    po = po_candidates[0] if len(po_candidates) == 1 else None
    if po is None:
        unperformed.update(("po_supplier_match", "po_status"))
    if po is not None:
        if supplier_id is not None and po.supplier_id != supplier_id:
            add(ReconciliationFindingCode.PO_SUPPLIER_MISMATCH, "purchase_order_reference", "Purchase order belongs to another supplier.", observed=supplier_id, reference=po.supplier_id, ids=(po.id,))
        if po.status not in policy.eligible_po_statuses:
            add(ReconciliationFindingCode.PO_INELIGIBLE, "purchase_order_reference", "Purchase order status is not eligible.", observed=po.status, reference=policy.eligible_po_statuses, ids=(po.id,))
    po_match = PurchaseOrderMatch(observed_reference=po_ref,
                                  purchase_order_ids=tuple(p.id for p in po_candidates))

    normalized = normalize_invoice_number(invoice.invoice_number.value)
    if normalized is None:
        unperformed.add("duplicate_identity")
    identity = [item for item in prior if supplier_id is not None and normalized is not None
                and item.supplier_id == supplier_id and item.invoice_number_normalized == normalized]
    hashes = [item for item in prior if item.source_sha256 == source_hash]
    identity_ids = {item.id for item in identity}
    hash_ids = {item.id for item in hashes}
    duplicate = DuplicateCheckResult(normalized_invoice_number=normalized,
                                     identity_invoice_ids=tuple(sorted(identity_ids)),
                                     hash_invoice_ids=tuple(sorted(hash_ids)))
    if identity_ids:
        add(ReconciliationFindingCode.DUPLICATE_INVOICE_IDENTITY, "invoice_number", "Supplier and normalized invoice number were ingested previously.", observed=normalized, ids=tuple(identity_ids))
    exact_ids = identity_ids & hash_ids
    conflicting_ids = {item.id for item in identity if item.source_sha256 is not None
                       and item.source_sha256 != source_hash}
    for item in identity:
        if item.source_sha256 is None:
            unperformed.add(f"duplicate_document_comparison:{item.id}")
    if exact_ids:
        add(ReconciliationFindingCode.EXACT_REPEAT_SUBMISSION, "invoice_number", "Same supplier invoice identity and document hash were ingested previously.", observed=normalized, ids=tuple(exact_ids))
    if conflicting_ids:
        add(ReconciliationFindingCode.CONFLICTING_INVOICE_DOCUMENT, "invoice_number", "Same supplier invoice identity has a different source document.", observed=normalized, ids=tuple(conflicting_ids))
    if hash_ids - exact_ids:
        add(ReconciliationFindingCode.DUPLICATE_DOCUMENT_HASH, "source_sha256", "Source document hash was ingested previously under another identity or upload.", observed=source_hash, ids=tuple(hash_ids - exact_ids))

    product_matches: list[ProductMatch] = []
    po_rows: dict[str, list[tuple[int, object, PurchaseOrderLineRecord]]] = defaultdict(list)
    if not invoice.lines:
        unperformed.add("invoice_lines")
    for line in sorted(invoice.lines, key=lambda item: item.source_row_order):
        row = line.source_row_order
        identifiers = [(kind, getattr(line, kind).value) for kind in ("upc", "ndc")
                       if getattr(line, kind).value is not None]
        candidates: set[str] | None = None
        if not identifiers:
            add(ReconciliationFindingCode.MISSING_PRODUCT_IDENTIFIER, "product_identifier", "No UPC or NDC was extracted.", row=row)
            if any(getattr(line, kind).evidence or getattr(line, kind).uncertainty
                   for kind in ("upc", "ndc")):
                add(ReconciliationFindingCode.UNREADABLE_PRODUCT_IDENTIFIER, "product_identifier", "Identifier region was observed but no value was extracted.", row=row)
        for kind, value in identifiers:
            if not _valid_identifier(kind, value):
                add(ReconciliationFindingCode.MALFORMED_PRODUCT_IDENTIFIER, kind, "Identifier is not a valid explicit UPC-A or 11-digit NDC.", row=row, observed=value)
                candidates = set()
                continue
            matches = {p.id for p in products if getattr(p, kind) == value}
            if not matches:
                add(ReconciliationFindingCode.UNKNOWN_PRODUCT, kind, "Identifier is absent from the trusted catalog.", row=row, observed=value)
            candidates = matches if candidates is None else candidates & matches
        if len(identifiers) > 1 and candidates == set() and all(
                any(getattr(p, kind) == value for p in products) for kind, value in identifiers
                ) and all(_valid_identifier(kind, value) for kind, value in identifiers):
            add(ReconciliationFindingCode.CONFLICTING_PRODUCT_IDENTIFIERS, "product_identifier", "UPC and NDC refer to different catalog products.", row=row)
        product_ids = tuple(sorted(candidates or ()))
        if len(product_ids) > 1:
            add(ReconciliationFindingCode.AMBIGUOUS_PRODUCT, "product_identifier", "Identifier matches multiple catalog products.", row=row, ids=product_ids)
        po_line_ids: tuple[str, ...] = ()
        if po is None or len(product_ids) != 1:
            unperformed.update((f"line[{row}].po_line", f"line[{row}].quantity",
                                f"line[{row}].unit_cost"))
        if po is not None and len(product_ids) == 1:
            matched_po_lines = [item for item in po.lines if item.product_id == product_ids[0]]
            po_line_ids = tuple(sorted(item.id for item in matched_po_lines))
            if not matched_po_lines:
                add(ReconciliationFindingCode.PRODUCT_NOT_ON_PO, "product_identifier", "Catalog product is absent from the referenced purchase order.", row=row, ids=(po.id, product_ids[0]))
            elif len(matched_po_lines) > 1:
                add(ReconciliationFindingCode.AMBIGUOUS_PO_LINE, "product_identifier", "Multiple PO lines contain this product; no line was silently selected.", row=row, ids=po_line_ids)
            else:
                po_rows[matched_po_lines[0].id].append((row, line, matched_po_lines[0]))
            if len(matched_po_lines) != 1:
                unperformed.update((f"line[{row}].quantity", f"line[{row}].unit_cost"))
        product_matches.append(ProductMatch(source_row_order=row, product_ids=product_ids,
                                             po_line_ids=po_line_ids))

    # Aggregate repeated invoice rows against a single PO product, without assuming
    # one invoice consumes the whole PO. Prior receipts reduce the available balance.
    for po_line_id in sorted(po_rows):
        entries = po_rows[po_line_id]
        reference_line = entries[0][2]
        quantities = [item.quantity.value for _, item, _ in entries]
        remaining = reference_line.ordered_quantity - reference_line.received_quantity
        if not reference_line.receipt_history_complete:
            add(ReconciliationFindingCode.INCONSISTENT_RECEIPT_HISTORY, "quantity", "Receipt history contains a movement that cannot be safely counted.", row=entries[0][0], ids=(po_line_id,))
            unperformed.add(f"po_line[{po_line_id}].remaining_quantity")
        if any(value is None for value in quantities):
            unperformed.add(f"po_line[{po_line_id}].remaining_quantity")
            for row, item, _ in entries:
                if item.quantity.value is None:
                    add(ReconciliationFindingCode.MISSING_MATCH_INPUT, "quantity", "Cannot compare quantity without an extracted value.", row=row, ids=(po_line_id,), severity=Severity.WARNING)
                    unperformed.add(f"line[{row}].quantity")
        elif reference_line.receipt_history_complete and sum(quantities) > remaining:
            add(ReconciliationFindingCode.PO_QUANTITY_MISMATCH, "quantity", "Invoice quantity exceeds remaining ordered quantity.", row=entries[0][0], observed=sum(quantities), reference=remaining, ids=(po_line_id,))
        for row, item, _ in entries:
            if item.unit_cost.value is None:
                add(ReconciliationFindingCode.MISSING_MATCH_INPUT, "unit_cost", "Cannot compare unit cost without an extracted value.", row=row, ids=(po_line_id,), severity=Severity.WARNING)
                unperformed.add(f"line[{row}].unit_cost")
            elif item.unit_cost.value != reference_line.expected_unit_cost:
                add(ReconciliationFindingCode.PO_PRICE_MISMATCH, "unit_cost", "Invoice unit cost differs from PO unit cost.", row=row, observed=item.unit_cost.value, reference=reference_line.expected_unit_cost, ids=(po_line_id,))

    findings.sort(key=lambda f: (f.source_row_order or 0, f.field, f.code.value, f.reference_ids))
    codes = {f.code for f in findings}
    decision = (ReconciliationDecision.REJECTED if validation.decision == ReviewDecision.REJECTED or codes.intersection(policy.reject_codes)
                else ReconciliationDecision.NEEDS_REVIEW if unperformed or any(f.manual_review_required for f in findings) or validation.decision != ReviewDecision.READY_FOR_PO_MATCHING
                else ReconciliationDecision.READY_FOR_POSTING_REVIEW)
    return ReconciliationResult(source_sha256=source_hash, findings=tuple(findings),
                                supplier_match=supplier_match, product_matches=tuple(product_matches),
                                purchase_order_match=po_match, duplicate_check=duplicate,
                                unperformed_checks=tuple(sorted(unperformed)),
                                validation_decision=validation.decision, decision=decision)
