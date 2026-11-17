"""Atomic PostgreSQL receipt posting after explicit, auditable staff approval."""

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker, selectinload

from meridian.domain import InvoiceOperation, InvoiceState, require_money, transition_invoice
from meridian.extraction import ExtractionResult
from meridian.models import (
    AuditEvent, InventoryBalance, InventoryMovement, Invoice, InvoiceDocument,
    InvoiceLine, InvoicePostingAuthorization, InvoiceUploadAttempt, Location,
    Product, PurchaseOrder, PurchaseOrderLine, Supplier,
)
from meridian.ocr import OcrDocument
from meridian.reconciliation import (
    ReconciliationDecision, ReconciliationFindingCode, ReconciliationResult,
    normalize_invoice_number,
    reconcile_invoice,
)
from meridian.reconciliation_repository import SqlAlchemyTrustedRepository
from meridian.synthetic import SyntheticModel
from meridian.validation import FindingCode, ReviewDecision, validate_invoice


class PostingStatus(StrEnum):
    POSTED = "posted"
    ALREADY_POSTED = "already_posted"


class PostingErrorCode(StrEnum):
    INVOICE_NOT_FOUND = "invoice_not_found"
    INVALID_INVOICE_STATE = "invalid_invoice_state"
    MISSING_APPROVAL_EVIDENCE = "missing_approval_evidence"
    STALE_RECONCILIATION = "stale_reconciliation"
    DUPLICATE_INVOICE_IDENTITY = "duplicate_invoice_identity"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    PO_NOT_FOUND = "po_not_found"
    PO_INELIGIBLE = "po_ineligible"
    SUPPLIER_MISMATCH = "supplier_mismatch"
    PRODUCT_MISMATCH = "product_mismatch"
    PRICE_MISMATCH = "price_mismatch"
    MISSING_PO_ALLOCATION = "missing_po_allocation"
    AMBIGUOUS_PO_ALLOCATION = "ambiguous_po_allocation"
    INSUFFICIENT_PO_QUANTITY = "insufficient_po_quantity"
    INVALID_RECEIPT_HISTORY = "invalid_receipt_history"
    INVALID_INVENTORY_LOCATION = "invalid_inventory_location"
    TRANSACTION_CONFLICT = "transaction_conflict"


class PostingConflict(Exception):
    def __init__(self, code: PostingErrorCode, message: str):
        self.code = code
        super().__init__(message)


class LineAllocation(SyntheticModel):
    invoice_line_id: UUID
    purchase_order_line_id: UUID
    source_row_order: int = Field(gt=0)


class AuthorizationRequest(SyntheticModel):
    invoice_id: UUID
    approval_event_id: UUID
    location_id: UUID
    allocations: tuple[LineAllocation, ...] = Field(min_length=1)


class PostingRequest(SyntheticModel):
    invoice_id: UUID
    authorization_id: UUID
    location_id: UUID
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    allocations: tuple[LineAllocation, ...] = Field(min_length=1)


class PostingApprovalEvidence(SyntheticModel):
    invoice_id: UUID
    location_id: UUID
    source_sha256: str
    invoice_fingerprint: str
    reference_fingerprint: str
    allocation_fingerprint: str
    evidence_fingerprint: str
    validation_decision: ReviewDecision
    reconciliation_decision: ReconciliationDecision
    resolved_po_line_rows: tuple[int, ...] = ()
    resolved_validation_codes: tuple[FindingCode, ...] = ()


class PostingResult(SyntheticModel):
    status: PostingStatus
    invoice_id: UUID
    authorization_id: UUID
    idempotency_key: str
    movement_ids: tuple[UUID, ...]


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


def _money_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, ".4f")


def _allocations(allocations: tuple[LineAllocation, ...]) -> tuple[LineAllocation, ...]:
    ordered = tuple(sorted(allocations, key=lambda item: str(item.invoice_line_id)))
    if (len({item.invoice_line_id for item in ordered}) != len(ordered)
            or len({item.source_row_order for item in ordered}) != len(ordered)):
        raise PostingConflict(PostingErrorCode.AMBIGUOUS_PO_ALLOCATION,
                              "Each invoice line and extracted source row needs one allocation.")
    return ordered


def _allocation_fingerprint(allocations: tuple[LineAllocation, ...]) -> str:
    return _digest([(str(item.invoice_line_id), str(item.purchase_order_line_id),
                     item.source_row_order) for item in _allocations(allocations)])


def _invoice_fingerprint(invoice: Invoice, lines: list[InvoiceLine]) -> str:
    return _digest({
        "id": str(invoice.id), "supplier": str(invoice.supplier_id),
        "number": invoice.invoice_number_normalized, "date": invoice.invoice_date,
        "subtotal": _money_text(invoice.subtotal), "tax": _money_text(invoice.tax),
        "total": _money_text(invoice.total),
        "lines": [(str(line.id), line.line_number, str(line.product_id), line.upc_raw,
                   line.ndc_raw, line.quantity, _money_text(line.unit_cost),
                   _money_text(line.line_total))
                  for line in sorted(lines, key=lambda item: str(item.id))],
    })


def _reference_fingerprint(session: Session, orders: list[PurchaseOrder],
                           lines: list[PurchaseOrderLine], *, lock: bool) -> str:
    supplier_query = select(Supplier).where(
        Supplier.id.in_({order.supplier_id for order in orders})).order_by(Supplier.id)
    product_query = select(Product).where(
        Product.id.in_({line.product_id for line in lines})).order_by(Product.id)
    if lock:
        supplier_query = supplier_query.with_for_update(read=True)
        product_query = product_query.with_for_update(read=True)
    suppliers = list(session.scalars(supplier_query))
    products = list(session.scalars(product_query))
    if (len(suppliers) != len({order.supplier_id for order in orders})
            or len(products) != len({line.product_id for line in lines})):
        raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                              "Trusted supplier or catalog record is missing.")
    return _digest({
        "suppliers": [(str(item.id), item.code, item.name) for item in suppliers],
        "products": [(str(item.id), item.name, item.upc_normalized,
                      item.ndc_normalized) for item in products],
        "orders": [(str(po.id), str(po.supplier_id), po.order_number, po.status)
                   for po in sorted(orders, key=lambda item: str(item.id))],
        "lines": [(str(line.id), str(line.purchase_order_id), str(line.product_id),
                   line.ordered_quantity, _money_text(line.expected_unit_cost))
                  for line in sorted(lines, key=lambda item: str(item.id))],
    })


def _load_invoice(session: Session, invoice_id: UUID, *, lock: bool) -> Invoice:
    query = select(Invoice).where(Invoice.id == invoice_id)
    if lock:
        query = query.with_for_update()
    invoice = session.scalar(query)
    if invoice is None:
        raise PostingConflict(PostingErrorCode.INVOICE_NOT_FOUND, "Invoice does not exist.")
    return invoice


def _load_allocated_rows(session: Session, invoice: Invoice,
                         allocations: tuple[LineAllocation, ...], *, lock: bool
                         ) -> tuple[list[InvoiceLine], list[PurchaseOrder], list[PurchaseOrderLine]]:
    ordered = _allocations(allocations)
    invoice_query = select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.id)
    if lock:
        invoice_query = invoice_query.with_for_update()
    invoice_lines = list(session.scalars(invoice_query))
    if not invoice_lines or {line.id for line in invoice_lines} != {item.invoice_line_id for item in ordered}:
        raise PostingConflict(PostingErrorCode.MISSING_PO_ALLOCATION,
                              "Every persisted invoice line needs exactly one trusted allocation.")
    po_line_ids = {item.purchase_order_line_id for item in ordered}
    pre_rows = list(session.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.id.in_(po_line_ids))))
    if len(pre_rows) != len(po_line_ids):
        raise PostingConflict(PostingErrorCode.PO_NOT_FOUND, "An allocated PO line does not exist.")
    po_ids = {line.purchase_order_id for line in pre_rows}
    order_query = select(PurchaseOrder).where(PurchaseOrder.id.in_(po_ids)).order_by(PurchaseOrder.id)
    if lock:
        order_query = order_query.with_for_update()
    orders = list(session.scalars(order_query))
    if len(orders) != len(po_ids):
        raise PostingConflict(PostingErrorCode.PO_NOT_FOUND, "An allocated purchase order does not exist.")
    line_query = select(PurchaseOrderLine).where(
        PurchaseOrderLine.purchase_order_id.in_(po_ids)).order_by(PurchaseOrderLine.id)
    if lock:
        line_query = line_query.with_for_update()
    po_lines = list(session.scalars(line_query))
    if (not po_line_ids.issubset({line.id for line in po_lines})
            or {line.purchase_order_id for line in po_lines} != po_ids):
        raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                              "PO line allocation changed while records were locked.")
    return invoice_lines, orders, po_lines


def _source_document_is_current(session: Session, invoice_id: UUID, source_hash: str) -> bool:
    hashes = set(session.scalars(
        select(InvoiceDocument.sha256).join(InvoiceUploadAttempt,
            InvoiceUploadAttempt.document_id == InvoiceDocument.id)
        .where(InvoiceUploadAttempt.invoice_id == invoice_id)))
    return hashes == {source_hash}


def prepare_posting_approval(session: Session, invoice_id: UUID, location_id: UUID,
                             extraction: ExtractionResult, ocr: OcrDocument,
                             allocations: tuple[LineAllocation, ...], *,
                             supplier_aliases: dict[str, tuple[str, ...]] | None = None
                             ) -> PostingApprovalEvidence:
    """Read-only re-establishment of Task 12 and 13 for a human approval event."""
    with session.no_autoflush:
        invoice = _load_invoice(session, invoice_id, lock=False)
        if invoice.status not in ("validated", "approved"):
            raise PostingConflict(PostingErrorCode.INVALID_INVOICE_STATE,
                                  "Only a validated or approved invoice can receive posting approval evidence.")
        if session.get(Location, location_id) is None:
            raise PostingConflict(PostingErrorCode.INVALID_INVENTORY_LOCATION,
                                  "Approval must identify an existing inventory location.")
        if not _source_document_is_current(session, invoice_id, extraction.source_sha256):
            raise PostingConflict(PostingErrorCode.MISSING_APPROVAL_EVIDENCE,
                                  "Invoice must link exactly the source document used by extraction.")
        validation = validate_invoice(extraction, ocr)
        reviewed_repeated_products = (validation.decision == ReviewDecision.NEEDS_REVIEW
            and bool(validation.findings)
            and all(finding.code == FindingCode.DUPLICATE_PRODUCT_IDENTIFIER
                    for finding in validation.findings))
        if validation.decision != ReviewDecision.READY_FOR_PO_MATCHING and not reviewed_repeated_products:
            raise PostingConflict(PostingErrorCode.MISSING_APPROVAL_EVIDENCE,
                                  "Invoice-local validation is not ready for PO matching.")
        reconciliation = reconcile_invoice(extraction, validation,
            SqlAlchemyTrustedRepository(session, supplier_aliases=supplier_aliases),
            current_invoice_id=invoice_id)
        ambiguous_rows = tuple(sorted({finding.source_row_order for finding in reconciliation.findings
            if finding.code == ReconciliationFindingCode.AMBIGUOUS_PO_LINE
            and finding.source_row_order is not None}))
        resolved_checks = {f"line[{row}].{field}" for row in ambiguous_rows
                           for field in ("quantity", "unit_cost")}
        explicit_ambiguity_only = (
            bool(ambiguous_rows)
            and all(finding.code == ReconciliationFindingCode.AMBIGUOUS_PO_LINE
                    for finding in reconciliation.findings)
            and set(reconciliation.unperformed_checks) == resolved_checks)
        reconciled_except_reviewed_warning = (
            reviewed_repeated_products and not reconciliation.findings
            and not reconciliation.unperformed_checks)
        if (reconciliation.decision != ReconciliationDecision.READY_FOR_POSTING_REVIEW
                and not explicit_ambiguity_only
                and not reconciled_except_reviewed_warning):
            raise PostingConflict(PostingErrorCode.MISSING_APPROVAL_EVIDENCE,
                                  "Supplier, PO, catalog, duplicate, or receipt checks are unresolved.")
        invoice_lines, orders, po_lines = _load_allocated_rows(session, invoice,
                                                               allocations, lock=False)
        _check_invoice_and_allocations(invoice, invoice_lines, orders, po_lines,
                                       allocations, extraction, reconciliation)
        _check_quantity_limits(session, invoice_lines, po_lines, allocations)
        return PostingApprovalEvidence(
            invoice_id=invoice_id, location_id=location_id,
            source_sha256=extraction.source_sha256,
            invoice_fingerprint=_invoice_fingerprint(invoice, invoice_lines),
            reference_fingerprint=_reference_fingerprint(session, orders, po_lines, lock=False),
            allocation_fingerprint=_allocation_fingerprint(allocations),
            evidence_fingerprint=_digest({"validation": validation.model_dump(mode="json"),
                                          "reconciliation": reconciliation.model_dump(mode="json")}),
            validation_decision=validation.decision,
            reconciliation_decision=reconciliation.decision,
            resolved_po_line_rows=ambiguous_rows if explicit_ambiguity_only else (),
            resolved_validation_codes=(FindingCode.DUPLICATE_PRODUCT_IDENTIFIER,)
                if reviewed_repeated_products else ())


def _check_invoice_and_allocations(invoice: Invoice, invoice_lines: list[InvoiceLine],
                                   orders: list[PurchaseOrder], po_lines: list[PurchaseOrderLine],
                                   allocations: tuple[LineAllocation, ...], extraction: ExtractionResult,
                                   reconciliation: ReconciliationResult) -> None:
    observed = extraction.invoice
    if (normalize_invoice_number(observed.invoice_number.value) != invoice.invoice_number_normalized
            or observed.invoice_date.value != invoice.invoice_date
            or observed.printed_subtotal.value != invoice.subtotal
            or observed.printed_tax.value != invoice.tax
            or observed.printed_total.value != invoice.total
            or str(invoice.supplier_id) not in reconciliation.supplier_match.supplier_ids):
        raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                              "Persisted invoice header differs from approved extraction.")
    if {str(order.id) for order in orders} != set(reconciliation.purchase_order_match.purchase_order_ids):
        raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                              "Allocated PO differs from the reconciled PO reference.")
    source_rows = {line.source_row_order: line for line in observed.lines}
    product_matches = {item.source_row_order: item for item in reconciliation.product_matches}
    invoice_by_id = {line.id: line for line in invoice_lines}
    po_by_id = {line.id: line for line in po_lines}
    order_by_id = {order.id: order for order in orders}
    if len(source_rows) != len(invoice_lines):
        raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                              "Persisted invoice lines differ from approved extraction.")
    for allocation in _allocations(allocations):
        line = invoice_by_id[allocation.invoice_line_id]
        po_line = po_by_id[allocation.purchase_order_line_id]
        source = source_rows.get(allocation.source_row_order)
        match = product_matches.get(allocation.source_row_order)
        if source is None or match is None or str(line.product_id) not in match.product_ids:
            raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                                  "Invoice line is not the product approved by reconciliation.")
        if (line.quantity != source.quantity.value or line.unit_cost != source.unit_cost.value
                or line.line_total != source.printed_line_total.value
                or line.upc_raw != source.upc.value or line.ndc_raw != source.ndc.value):
            raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                                  "Persisted invoice line differs from approved extraction.")
        if (line.quantity is None or line.quantity <= 0 or line.unit_cost is None
                or line.line_total is None or line.line_total != line.quantity * line.unit_cost):
            raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                                  "Persisted invoice line has incomplete or incorrect arithmetic.")
        if str(po_line.id) not in match.po_line_ids:
            raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                                  "Allocated PO line was not reconciled to this invoice line.")
        order = order_by_id[po_line.purchase_order_id]
        if order.supplier_id != invoice.supplier_id or order.status != "open":
            raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                                  "PO ownership or status changed before authorization.")
        if line.product_id != po_line.product_id or line.unit_cost != po_line.expected_unit_cost:
            raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                                  "PO product or price differs from approved invoice line.")


def record_posting_authorization(session: Session, request: AuthorizationRequest,
                                 extraction: ExtractionResult,
                                 ocr: OcrDocument, *,
                                 supplier_aliases: dict[str, tuple[str, ...]] | None = None
                                 ) -> InvoicePostingAuthorization:
    """Store evidence only after an existing staff approval event binds the same facts.

    Caller owns the transaction; this function flushes but never commits or changes
    invoice state. The upstream human workflow authenticates the audit event actor.
    """
    invoice = _load_invoice(session, request.invoice_id, lock=True)
    if invoice.status != "approved":
        raise PostingConflict(PostingErrorCode.INVALID_INVOICE_STATE,
                              "Invoice must be approved before posting authorization is recorded.")
    evidence = prepare_posting_approval(session, request.invoice_id, request.location_id, extraction,
                                        ocr, request.allocations,
                                        supplier_aliases=supplier_aliases)
    event = session.get(AuditEvent, request.approval_event_id)
    if (event is None or event.actor_type != "staff" or not event.actor_id
            or event.action != "approve" or event.entity_type != "invoice"
            or event.entity_id != request.invoice_id
            or event.after_data != {"status": "approved",
                                    "posting_evidence": evidence.model_dump(mode="json")}):
        raise PostingConflict(PostingErrorCode.MISSING_APPROVAL_EVIDENCE,
                              "A staff approval event must bind the exact current evidence.")
    if session.scalar(select(InvoicePostingAuthorization.id).where(
            InvoicePostingAuthorization.invoice_id == request.invoice_id)) is not None:
        raise PostingConflict(PostingErrorCode.IDEMPOTENCY_CONFLICT,
                              "Invoice already has a posting authorization.")
    authorization = InvoicePostingAuthorization(
        invoice_id=request.invoice_id, approval_event_id=request.approval_event_id,
        location_id=request.location_id,
        source_sha256=evidence.source_sha256,
        invoice_fingerprint=evidence.invoice_fingerprint,
        reference_fingerprint=evidence.reference_fingerprint,
        allocation_fingerprint=evidence.allocation_fingerprint,
        evidence_fingerprint=evidence.evidence_fingerprint,
        approval_payload_sha256=_digest(evidence.model_dump(mode="json")))
    session.add(authorization)
    try:
        session.flush()
    except IntegrityError as exc:
        raise PostingConflict(PostingErrorCode.IDEMPOTENCY_CONFLICT,
                              "Invoice or staff event already has a posting authorization.") from exc
    return authorization


def _request_fingerprint(request: PostingRequest) -> str:
    return _digest({"invoice": request.invoice_id, "authorization": request.authorization_id,
                    "location": request.location_id, "key": request.idempotency_key,
                    "allocations": _allocation_fingerprint(request.allocations)})


def _advisory_lock(session: Session, *values: str) -> None:
    # Stable 64-bit keys, ordered to avoid cross-invoice advisory-lock deadlocks.
    keys = sorted({int.from_bytes(hashlib.sha256(value.encode()).digest()[:8],
                                 "big", signed=True) for value in values})
    for key in keys:
        session.execute(select(func.pg_advisory_xact_lock(key)))


def _check_duplicate_records(session: Session, invoice: Invoice,
                             authorization: InvoicePostingAuthorization) -> None:
    number = normalize_invoice_number(invoice.invoice_number_normalized)
    if number is None:
        raise PostingConflict(PostingErrorCode.DUPLICATE_INVOICE_IDENTITY,
                              "Invoice number is required for posting.")
    _advisory_lock(session, f"identity:{invoice.supplier_id}:{number}",
                   f"document:{authorization.source_sha256}")
    others = session.scalars(select(Invoice).where(
        Invoice.supplier_id == invoice.supplier_id, Invoice.id != invoice.id,
        Invoice.status.notin_(("failed", "rejected")))).all()
    if any(normalize_invoice_number(other.invoice_number_normalized) == number for other in others):
        raise PostingConflict(PostingErrorCode.DUPLICATE_INVOICE_IDENTITY,
                              "Another active invoice has the same supplier and normalized number.")
    document_uses = session.execute(select(InvoiceUploadAttempt.invoice_id)
        .join(InvoiceDocument, InvoiceUploadAttempt.document_id == InvoiceDocument.id)
        .where(InvoiceDocument.sha256 == authorization.source_sha256)).all()
    for (other_id,) in document_uses:
        if other_id == invoice.id:
            continue
        if other_id is None:
            raise PostingConflict(PostingErrorCode.DUPLICATE_INVOICE_IDENTITY,
                                  "An unresolved upload uses this source document.")
        other = session.get(Invoice, other_id)
        if other is not None and other.status not in ("failed", "rejected"):
            raise PostingConflict(PostingErrorCode.DUPLICATE_INVOICE_IDENTITY,
                                  "Another active invoice uses this source document.")


def _receipt_totals(session: Session, po_lines: list[PurchaseOrderLine]) -> dict[UUID, int]:
    po_ids = {line.id for line in po_lines}
    receipts = list(session.scalars(select(InventoryMovement).options(
        selectinload(InventoryMovement.invoice_line).selectinload(InvoiceLine.invoice))
        .where(InventoryMovement.movement_type == "receipt",
               InventoryMovement.purchase_order_line_id.in_(po_ids))))
    receipt_ids = {item.id for item in receipts}
    reversals = list(session.scalars(select(InventoryMovement).where(
        InventoryMovement.movement_type == "reversal",
        InventoryMovement.reverses_movement_id.in_(receipt_ids)))) if receipt_ids else []
    reversed_by: dict[UUID, list[InventoryMovement]] = defaultdict(list)
    for reversal in reversals:
        reversed_by[reversal.reverses_movement_id].append(reversal)
    totals: dict[UUID, int] = defaultdict(int)
    po_by_id = {line.id: line for line in po_lines}
    for receipt in receipts:
        source = receipt.invoice_line
        po_line = po_by_id[receipt.purchase_order_line_id]
        if (source is None or source.invoice.status != "posted"
                or source.invoice.supplier_id != session.get(PurchaseOrder, po_line.purchase_order_id).supplier_id
                or source.id != receipt.invoice_line_id
                or source.product_id != receipt.product_id
                or po_line.product_id != receipt.product_id
                or source.quantity != receipt.quantity_delta or receipt.quantity_delta <= 0
                or source.unit_cost != receipt.unit_cost):
            raise PostingConflict(PostingErrorCode.INVALID_RECEIPT_HISTORY,
                                  "Historical receipt disagrees with its posted invoice or PO line.")
        reverses = reversed_by.get(receipt.id, [])
        if len(reverses) > 1 or any(
                item.quantity_delta != -receipt.quantity_delta
                or item.product_id != receipt.product_id
                or item.location_id != receipt.location_id for item in reverses):
            raise PostingConflict(PostingErrorCode.INVALID_RECEIPT_HISTORY,
                                  "Historical receipt reversal is incomplete or inconsistent.")
        totals[po_line.id] += receipt.quantity_delta + sum(item.quantity_delta for item in reverses)
    return totals


def _check_quantity_limits(session: Session, invoice_lines: list[InvoiceLine],
                           po_lines: list[PurchaseOrderLine],
                           allocations: tuple[LineAllocation, ...]) -> None:
    posted_quantities = _receipt_totals(session, po_lines)
    invoice_by_id = {line.id: line for line in invoice_lines}
    po_by_id = {line.id: line for line in po_lines}
    requested: dict[UUID, int] = defaultdict(int)
    for allocation in allocations:
        quantity = invoice_by_id[allocation.invoice_line_id].quantity
        if quantity is None or quantity <= 0:
            raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                                  "Invoice line quantity is missing or invalid.")
        requested[allocation.purchase_order_line_id] += quantity
    for po_line_id, amount in requested.items():
        po_line = po_by_id[po_line_id]
        if amount > po_line.ordered_quantity - posted_quantities.get(po_line_id, 0):
            raise PostingConflict(PostingErrorCode.INSUFFICIENT_PO_QUANTITY,
                                  "Requested receipt exceeds remaining PO quantity.")


def _authorized_result(session: Session, request: PostingRequest,
                       authorization: InvoicePostingAuthorization,
                       status: PostingStatus) -> PostingResult:
    line_ids = {item.invoice_line_id for item in request.allocations}
    invoice_lines = {item.id: item for item in session.scalars(select(InvoiceLine).where(
        InvoiceLine.id.in_(line_ids)))}
    movements = list(session.scalars(select(InventoryMovement).where(
        InventoryMovement.movement_type == "receipt",
        InventoryMovement.invoice_line_id.in_(line_ids)).order_by(InventoryMovement.invoice_line_id)))
    expected_po = {item.invoice_line_id: item.purchase_order_line_id
                   for item in request.allocations}
    if (len(movements) != len(line_ids) or len(invoice_lines) != len(line_ids)
            or any(item.invoice_line_id not in invoice_lines
                   or item.purchase_order_line_id != expected_po[item.invoice_line_id]
                   or item.product_id != invoice_lines[item.invoice_line_id].product_id
                   or item.quantity_delta != invoice_lines[item.invoice_line_id].quantity
                   or item.unit_cost != invoice_lines[item.invoice_line_id].unit_cost
                   or item.location_id != request.location_id
                   or item.posting_key != f"receipt:{request.invoice_id}:{item.invoice_line_id}"
                   for item in movements)):
        raise PostingConflict(PostingErrorCode.INVALID_RECEIPT_HISTORY,
                              "Posted invoice lacks a complete and consistent receipt ledger.")
    return PostingResult(status=status, invoice_id=request.invoice_id,
                         authorization_id=authorization.id,
                         idempotency_key=request.idempotency_key,
                         movement_ids=tuple(item.id for item in movements))


def _post_in_transaction(session: Session, request: PostingRequest) -> PostingResult:
    if session.get_bind().dialect.name != "postgresql":
        raise PostingConflict(PostingErrorCode.TRANSACTION_CONFLICT,
                              "Posting requires PostgreSQL row and advisory locks.")
    invoice = _load_invoice(session, request.invoice_id, lock=True)
    authorization = session.scalar(select(InvoicePostingAuthorization).where(
        InvoicePostingAuthorization.invoice_id == invoice.id).with_for_update())
    if authorization is None or authorization.id != request.authorization_id:
        raise PostingConflict(PostingErrorCode.MISSING_APPROVAL_EVIDENCE,
                              "Invoice has no matching persisted approval evidence.")
    request_fingerprint = _request_fingerprint(request)
    if authorization.posted_at is not None:
        if (authorization.idempotency_key == request.idempotency_key
                and authorization.request_fingerprint == request_fingerprint
                and invoice.status == "posted"):
            return _authorized_result(session, request, authorization,
                                      PostingStatus.ALREADY_POSTED)
        raise PostingConflict(PostingErrorCode.IDEMPOTENCY_CONFLICT,
                              "Posted invoice cannot be submitted with changed request contents or key.")
    if authorization.idempotency_key is not None or authorization.request_fingerprint is not None:
        raise PostingConflict(PostingErrorCode.TRANSACTION_CONFLICT,
                              "Posting authorization has an incomplete prior transaction.")
    if invoice.status != "approved":
        raise PostingConflict(PostingErrorCode.INVALID_INVOICE_STATE,
                              "First posting requires an approved invoice.")
    if request.location_id != authorization.location_id:
        raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                              "Target location differs from staff-approved location.")
    if _allocation_fingerprint(request.allocations) != authorization.allocation_fingerprint:
        raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                              "Posting allocations differ from staff-approved allocations.")
    if not _source_document_is_current(session, invoice.id, authorization.source_sha256):
        raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                              "Source document changed after approval.")
    event = session.get(AuditEvent, authorization.approval_event_id)
    if (event is None or event.actor_type != "staff" or not event.actor_id
            or event.action != "approve" or event.entity_type != "invoice"
            or event.entity_id != invoice.id
            or event.after_data is None
            or event.after_data.get("status") != "approved"
            or _digest(event.after_data.get("posting_evidence"))
               != authorization.approval_payload_sha256):
        raise PostingConflict(PostingErrorCode.MISSING_APPROVAL_EVIDENCE,
                              "Staff approval evidence is missing or changed.")
    if session.get(Location, request.location_id) is None:
        raise PostingConflict(PostingErrorCode.INVALID_INVENTORY_LOCATION,
                              "Target inventory location does not exist.")
    _check_duplicate_records(session, invoice, authorization)
    if session.scalar(select(InvoicePostingAuthorization.id).where(
            InvoicePostingAuthorization.idempotency_key == request.idempotency_key,
            InvoicePostingAuthorization.invoice_id != invoice.id)) is not None:
        raise PostingConflict(PostingErrorCode.IDEMPOTENCY_CONFLICT,
                              "Idempotency key belongs to another invoice.")

    invoice_lines, orders, po_lines = _load_allocated_rows(session, invoice,
                                                           request.allocations, lock=True)
    invoice_by_id = {line.id: line for line in invoice_lines}
    po_by_id = {line.id: line for line in po_lines}
    order_by_id = {po.id: po for po in orders}
    if _invoice_fingerprint(invoice, invoice_lines) != authorization.invoice_fingerprint:
        raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                              "Invoice values changed after approval.")
    for allocation in _allocations(request.allocations):
        line = invoice_by_id[allocation.invoice_line_id]
        po_line = po_by_id[allocation.purchase_order_line_id]
        order = order_by_id[po_line.purchase_order_id]
        if order.status != "open":
            raise PostingConflict(PostingErrorCode.PO_INELIGIBLE,
                                  "Purchase order no longer permits receiving.")
        if order.supplier_id != invoice.supplier_id:
            raise PostingConflict(PostingErrorCode.SUPPLIER_MISMATCH,
                                  "Allocated purchase order belongs to another supplier.")
        if line.product_id is None or line.product_id != po_line.product_id:
            raise PostingConflict(PostingErrorCode.PRODUCT_MISMATCH,
                                  "Invoice product differs from allocated PO product.")
        if line.quantity is None or line.quantity <= 0 or line.unit_cost is None:
            raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                                  "Invoice line lacks a valid quantity or unit cost.")
        try:
            require_money(line.unit_cost)
        except ValueError as exc:
            raise PostingConflict(PostingErrorCode.PRICE_MISMATCH,
                                  "Invoice unit cost is invalid.") from exc
        if line.unit_cost != po_line.expected_unit_cost:
            raise PostingConflict(PostingErrorCode.PRICE_MISMATCH,
                                  "PO unit price changed or differs from invoice.")
    if _reference_fingerprint(session, orders, po_lines, lock=True) != authorization.reference_fingerprint:
        raise PostingConflict(PostingErrorCode.STALE_RECONCILIATION,
                              "PO reference facts changed after approval.")
    _check_quantity_limits(session, invoice_lines, po_lines, request.allocations)
    line_ids = {line.id for line in invoice_lines}
    if session.scalar(select(InventoryMovement.id).where(
            InventoryMovement.movement_type == "receipt",
            InventoryMovement.invoice_line_id.in_(line_ids)).limit(1)) is not None:
        raise PostingConflict(PostingErrorCode.INVALID_RECEIPT_HISTORY,
                              "Approved invoice already has a receipt movement.")

    movement_ids: list[UUID] = []
    increments: dict[UUID, int] = defaultdict(int)
    for allocation in _allocations(request.allocations):
        line = invoice_by_id[allocation.invoice_line_id]
        movement = InventoryMovement(
            product_id=line.product_id, location_id=request.location_id,
            movement_type="receipt", quantity_delta=line.quantity, unit_cost=line.unit_cost,
            posting_key=f"receipt:{invoice.id}:{line.id}",
            invoice_line_id=line.id,
            purchase_order_line_id=allocation.purchase_order_line_id)
        session.add(movement)
        session.flush()  # Still inside the same transaction; any later failure rolls this back.
        movement_ids.append(movement.id)
        increments[line.product_id] += line.quantity
    for product_id in sorted(increments):
        amount = increments[product_id]
        statement = pg_insert(InventoryBalance).values(
            product_id=product_id, location_id=request.location_id,
            available_quantity=amount)
        statement = statement.on_conflict_do_update(
            index_elements=[InventoryBalance.product_id, InventoryBalance.location_id],
            set_={"available_quantity": InventoryBalance.available_quantity + amount,
                  "updated_at": func.now()})
        session.execute(statement)
    invoice.status = transition_invoice(InvoiceState(invoice.status),
                                        InvoiceOperation.MARK_POSTED).value
    authorization.idempotency_key = request.idempotency_key
    authorization.request_fingerprint = request_fingerprint
    authorization.posted_at = datetime.now(timezone.utc)
    session.flush()
    return PostingResult(status=PostingStatus.POSTED, invoice_id=invoice.id,
                         authorization_id=authorization.id,
                         idempotency_key=request.idempotency_key,
                         movement_ids=tuple(movement_ids))


def post_invoice(factory: sessionmaker[Session], request: PostingRequest) -> PostingResult:
    """Own exactly one transaction; never commits a partial receipt or state change."""
    try:
        with factory.begin() as session:
            return _post_in_transaction(session, request)
    except PostingConflict:
        raise
    except IntegrityError as exc:
        constraint = getattr(getattr(exc, "orig", None), "diag", None)
        name = getattr(constraint, "constraint_name", "")
        code = (PostingErrorCode.IDEMPOTENCY_CONFLICT if name in (
            "invoice_posting_authorizations_idempotency_key_key",
            "uq_inventory_movements_receipt_invoice_line",
            "inventory_movements_posting_key_key")
            else PostingErrorCode.TRANSACTION_CONFLICT)
        raise PostingConflict(code, "Database uniqueness or integrity safeguard rejected posting.") from exc
    except DBAPIError as exc:
        raise PostingConflict(PostingErrorCode.TRANSACTION_CONFLICT,
                              "Database transaction could not complete.") from exc
    except Exception as exc:
        raise PostingConflict(PostingErrorCode.TRANSACTION_CONFLICT,
                              "Posting transaction failed and was rolled back.") from exc
