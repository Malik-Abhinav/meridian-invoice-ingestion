"""Staff corrections, explicit review, and Task 14 authorization integration."""

import json
from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from meridian.api.errors import ApiProblem
from meridian.domain import InvoiceOperation, InvoiceState, transition_invoice
from meridian.extraction import ExtractionResult, Money
from meridian.inventory_posting import (AuthorizationRequest, LineAllocation, PostingConflict,
                                        prepare_posting_approval, record_posting_authorization)
from meridian.models import (AuditEvent, Invoice, InvoiceLine, InvoicePostingAuthorization,
                             InvoiceProcessingAttempt, InvoiceReviewState, Location, Product, Supplier)
from meridian.ocr import OcrDocument
from meridian.reconciliation import reconcile_invoice, normalize_invoice_number
from meridian.reconciliation_repository import SqlAlchemyTrustedRepository
from meridian.validation import ReviewDecision, validate_invoice


class LineCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_row_order: int = Field(gt=0)
    product_id: UUID | None = None
    upc: str | None = None
    ndc: str | None = None
    quantity: int | None = Field(default=None, gt=0)
    unit_cost: Money | None = None
    printed_line_total: Money | None = None

    @field_validator("unit_cost", "printed_line_total")
    @classmethod
    def nonnegative_money(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("corrected money must be nonnegative")
        return value


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(gt=0)
    supplier_id: UUID | None = None
    invoice_number: str | None = Field(default=None, min_length=1, max_length=120)
    invoice_date: date | None = None
    purchase_order_reference: str | None = Field(default=None, min_length=1, max_length=120)
    printed_subtotal: Money | None = None
    printed_tax: Money | None = None
    printed_total: Money | None = None
    lines: tuple[LineCorrection, ...] = ()

    @field_validator("printed_subtotal", "printed_tax", "printed_total")
    @classmethod
    def nonnegative_money(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("corrected money must be nonnegative")
        return value


class AllocationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_line_id: UUID
    purchase_order_line_id: UUID
    source_row_order: int = Field(gt=0)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(gt=0)
    location_id: UUID
    allocations: tuple[AllocationBody, ...] = Field(min_length=1)


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(gt=0)
    reason: str = Field(min_length=3, max_length=500)


def _wrap(wrapped, value):
    # Staff values are separate from OCR evidence; no synthetic OCR citation is created.
    return wrapped.model_copy(update={"value": value, "evidence": (), "uncertainty": None})


def _current(session: Session, invoice_id: UUID, *, lock: bool):
    query = select(Invoice).where(Invoice.id == invoice_id)
    if lock:
        query = query.with_for_update()
    invoice = session.scalar(query)
    if invoice is None:
        raise ApiProblem(404, "invoice_not_found", "Invoice does not exist.")
    review_query = select(InvoiceReviewState).where(InvoiceReviewState.invoice_id == invoice_id)
    if lock:
        review_query = review_query.with_for_update()
    review = session.scalar(review_query)
    if review is None:
        raise ApiProblem(409, "review_missing", "Invoice has no processing review snapshot.")
    run = session.get(InvoiceProcessingAttempt, review.processing_id)
    if run is None or run.ocr_json is None:
        raise ApiProblem(409, "evidence_missing", "Original OCR evidence is unavailable.")
    return invoice, review, run


def apply_correction(factory: sessionmaker[Session], invoice_id: UUID,
                     request: CorrectionRequest, actor: str) -> dict:
    if not request.model_fields_set.intersection({"supplier_id", "invoice_number", "invoice_date",
        "purchase_order_reference", "printed_subtotal", "printed_tax", "printed_total", "lines"}):
        raise ApiProblem(400, "empty_correction", "No correction fields were provided.")
    if request.model_fields_set <= {"expected_revision", "lines"} and (
            not request.lines or all(not line.model_fields_set.difference({"source_row_order"})
                                     for line in request.lines)):
        raise ApiProblem(400, "empty_correction", "No correction values were provided.")
    try:
        with factory.begin() as session:
            invoice, review, run = _current(session, invoice_id, lock=True)
            if invoice.status not in ("review_required", "validated"):
                raise ApiProblem(409, "invalid_invoice_state", "Invoice cannot be corrected in its current state.")
            if review.revision != request.expected_revision:
                raise ApiProblem(409, "revision_conflict", "Invoice review revision changed.")
            if session.scalar(select(InvoicePostingAuthorization.id).where(
                    InvoicePostingAuthorization.invoice_id == invoice_id)) is not None:
                raise ApiProblem(409, "approval_exists", "An approved posting authorization already exists.")
            before = {"invoice": invoice.status,
                      "extraction": review.current_extraction_json,
                      "revision": review.revision}
            extraction = ExtractionResult.model_validate_json(json.dumps(review.current_extraction_json))
            observed = extraction.invoice
            changed = set(review.corrected_fields_json)
            updates = {}
            if "supplier_id" in request.model_fields_set:
                supplier = session.get(Supplier, request.supplier_id)
                if supplier is None:
                    raise ApiProblem(422, "unknown_supplier", "Corrected supplier does not exist.")
                invoice.supplier_id = supplier.id
                updates["supplier_name"] = _wrap(observed.supplier_name, supplier.name)
                changed.add("supplier_name")
            for name in ("invoice_number", "invoice_date", "purchase_order_reference",
                         "printed_subtotal", "printed_tax", "printed_total"):
                if name in request.model_fields_set:
                    value = getattr(request, name)
                    if value is None:
                        raise ApiProblem(422, "invalid_correction", "Corrected fields cannot be null.")
                    updates[name] = _wrap(getattr(observed, name), value)
                    changed.add(name)
                    if name == "invoice_number":
                        invoice.invoice_number_raw = value
                        invoice.invoice_number_normalized = normalize_invoice_number(value)
                    elif name == "invoice_date":
                        invoice.invoice_date = value
                    elif name in ("printed_subtotal", "printed_tax", "printed_total"):
                        setattr(invoice, {"printed_subtotal": "subtotal", "printed_tax": "tax",
                                          "printed_total": "total"}[name], value)
            lines = list(observed.lines)
            stored_lines = list(session.scalars(select(InvoiceLine).where(
                InvoiceLine.invoice_id == invoice_id).order_by(InvoiceLine.line_number)))
            if len(lines) != len(stored_lines):
                raise ApiProblem(409, "stale_processing", "Persisted line count differs from extraction.")
            by_row = {line.source_row_order: index for index, line in enumerate(lines)}
            seen = set()
            for correction in request.lines:
                row = correction.source_row_order
                if row in seen or row not in by_row:
                    raise ApiProblem(422, "invalid_line", "Correction refers to an absent or repeated source row.")
                seen.add(row)
                index = by_row[row]
                source, stored = lines[index], stored_lines[index]
                line_updates = {}
                for field in ("upc", "ndc", "quantity", "unit_cost", "printed_line_total"):
                    if field in correction.model_fields_set:
                        value = getattr(correction, field)
                        if value is None:
                            raise ApiProblem(422, "invalid_correction", "Corrected line fields cannot be null.")
                        line_updates[field] = _wrap(getattr(source, field), value)
                        changed.add(f"line[{row}].{field}")
                        setattr(stored, {"printed_line_total": "line_total"}.get(field, field + "_raw"
                                if field in ("upc", "ndc") else field), value)
                if "product_id" in correction.model_fields_set:
                    product = session.get(Product, correction.product_id)
                    if product is None:
                        raise ApiProblem(422, "unknown_product", "Corrected product does not exist.")
                    stored.product_id = product.id
                    changed.add(f"line[{row}].product_id")
                lines[index] = source.model_copy(update=line_updates)
            updates["lines"] = tuple(lines)
            extraction = extraction.model_copy(update={"invoice": observed.model_copy(update=updates)})
            ocr = OcrDocument.model_validate_json(json.dumps(run.ocr_json))
            validation = validate_invoice(extraction, ocr)
            reconciliation = reconcile_invoice(extraction, validation,
                SqlAlchemyTrustedRepository(session), current_invoice_id=invoice_id)
            invoice.status = transition_invoice(InvoiceState(invoice.status), InvoiceOperation.CORRECT).value
            invoice.status = transition_invoice(InvoiceState.EXTRACTED,
                InvoiceOperation.VALIDATION_PASSED if validation.decision == ReviewDecision.READY_FOR_PO_MATCHING
                else InvoiceOperation.VALIDATION_REQUIRES_REVIEW).value
            review.revision += 1
            review.current_extraction_json = extraction.model_dump(mode="json")
            review.validation_json = validation.model_dump(mode="json")
            review.reconciliation_json = reconciliation.model_dump(mode="json")
            review.corrected_fields_json = sorted(changed)
            session.add(AuditEvent(actor_type="staff", actor_id=actor, action="correct",
                entity_type="invoice", entity_id=invoice_id, before_data=before,
                after_data={"status": invoice.status, "revision": review.revision,
                            "corrected_fields": sorted(changed),
                            "extraction": extraction.model_dump(mode="json")}))
            return {"invoice_id": invoice_id, "status": invoice.status,
                    "revision": review.revision, "validation": review.validation_json,
                    "reconciliation": review.reconciliation_json,
                    "corrected_fields": review.corrected_fields_json}
    except IntegrityError as exc:
        raise ApiProblem(409, "invoice_identity_conflict", "Corrected invoice identity conflicts with an existing invoice.") from exc


def approve_invoice(factory: sessionmaker[Session], invoice_id: UUID,
                    request: ApprovalRequest, actor: str) -> dict:
    allocations = tuple(LineAllocation(**item.model_dump()) for item in request.allocations)
    with factory.begin() as session:
        invoice, review, run = _current(session, invoice_id, lock=True)
        if review.revision != request.expected_revision:
            raise ApiProblem(409, "revision_conflict", "Invoice review revision changed.")
        if invoice.status not in ("review_required", "validated"):
            raise ApiProblem(409, "invalid_invoice_state", "Invoice is not ready for staff approval.")
        if invoice.status == "review_required":
            invoice.status = transition_invoice(InvoiceState.REVIEW_REQUIRED,
                                                InvoiceOperation.HUMAN_VALIDATE).value
        extraction = ExtractionResult.model_validate_json(json.dumps(review.current_extraction_json))
        ocr = OcrDocument.model_validate_json(json.dumps(run.ocr_json))
        approved_fields = tuple(field for field in review.corrected_fields_json
                                if not field.endswith(".product_id"))
        try:
            evidence = prepare_posting_approval(session, invoice_id, request.location_id,
                extraction, ocr, allocations, reviewed_fields=approved_fields)
        except PostingConflict as exc:
            raise ApiProblem(409, exc.code.value, str(exc)) from exc
        invoice.status = transition_invoice(InvoiceState.VALIDATED,
                                            InvoiceOperation.HUMAN_APPROVE).value
        event = AuditEvent(actor_type="staff", actor_id=actor, action="approve",
            entity_type="invoice", entity_id=invoice_id,
            before_data={"status": "validated", "revision": review.revision},
            after_data={"status": "approved", "posting_evidence": evidence.model_dump(mode="json")})
        session.add(event)
        session.flush()
        try:
            authorization = record_posting_authorization(session,
                AuthorizationRequest(invoice_id=invoice_id, approval_event_id=event.id,
                    location_id=request.location_id, allocations=allocations,
                    reviewed_fields=approved_fields), extraction, ocr)
        except PostingConflict as exc:
            raise ApiProblem(409, exc.code.value, str(exc)) from exc
        review.approved_allocations_json = [item.model_dump(mode="json") for item in request.allocations]
        return {"invoice_id": invoice_id, "status": "approved", "revision": review.revision,
                "authorization_id": authorization.id, "approval_event_id": event.id,
                "location_id": request.location_id, "evidence": evidence.model_dump(mode="json")}


def reject_invoice(factory: sessionmaker[Session], invoice_id: UUID,
                   request: RejectRequest, actor: str) -> dict:
    with factory.begin() as session:
        invoice, review, _ = _current(session, invoice_id, lock=True)
        if review.revision != request.expected_revision:
            raise ApiProblem(409, "revision_conflict", "Invoice review revision changed.")
        if invoice.status not in ("review_required", "validated"):
            raise ApiProblem(409, "invalid_invoice_state", "Invoice cannot be rejected in its current state.")
        before = invoice.status
        invoice.status = transition_invoice(InvoiceState(before), InvoiceOperation.HUMAN_REJECT).value
        session.add(AuditEvent(actor_type="staff", actor_id=actor, action="reject",
            entity_type="invoice", entity_id=invoice_id,
            before_data={"status": before}, after_data={"status": invoice.status,
                                                        "reason": request.reason}))
        return {"invoice_id": invoice_id, "status": invoice.status, "revision": review.revision}
