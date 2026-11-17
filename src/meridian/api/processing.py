"""Durable upload processing; expensive OCR/provider work occurs outside transactions."""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from meridian.api.config import ApiSettings
from meridian.api.errors import ApiProblem
from meridian.api.storage import LocalPdfStorage, StorageError
from meridian.domain import InvoiceOperation, InvoiceState, transition_invoice
from meridian.extraction import ExtractionError, ExtractionResult, ProviderError, extract_invoice
from meridian.extraction_providers import MockProvider, OpenAIProvider
from meridian.models import (AuditEvent, Invoice, InvoiceDocument, InvoiceLine,
                             InvoiceProcessingAttempt, InvoiceReviewState, InvoiceUploadAttempt)
from meridian.ocr import OcrDocument, OcrError, PreprocessingConfig, extract_ocr
from meridian.reconciliation import normalize_invoice_number, reconcile_invoice
from meridian.reconciliation_repository import SqlAlchemyTrustedRepository
from meridian.validation import ReviewDecision, ValidationResult, validate_invoice


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def config_fingerprint(settings: ApiSettings) -> str:
    value = {"provider": settings.provider,
             "model": ("ocr-only-v1" if settings.provider == "mock" else
                       settings.openai_model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
             "ocr": PreprocessingConfig().model_dump(mode="json"),
             "validation_policy": "task12-default-v1", "reconciliation_policy": "task13-default-v1"}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def processing_view(row: InvoiceProcessingAttempt) -> dict:
    return {"id": row.id, "upload_attempt_id": row.upload_attempt_id,
            "document_id": row.document_id, "invoice_id": row.invoice_id,
            "source_sha256": row.source_sha256, "config_fingerprint": row.config_fingerprint,
            "status": row.status, "attempt_count": row.attempt_count,
            "retryable": row.retryable, "provider": row.provider,
            "model_name": row.model_name, "ocr": row.ocr_json,
            "extraction": row.extraction_json, "validation": row.validation_json,
            "reconciliation": row.reconciliation_json,
            "failure": None if row.failure_code is None else {"code": row.failure_code,
                                                            "detail": row.failure_detail},
            "started_at": row.started_at, "completed_at": row.completed_at}


def _provider(settings: ApiSettings):
    if settings.provider == "mock":
        return MockProvider()
    if not os.getenv("OPENAI_API_KEY"):
        raise ApiProblem(409, "provider_not_configured", "Configured extraction provider lacks credentials.")
    return OpenAIProvider(model_name=settings.openai_model)


def _save_stage(factory: sessionmaker[Session], run_id: UUID, lease: UUID,
                **fields: object) -> None:
    with factory.begin() as session:
        row = session.scalar(select(InvoiceProcessingAttempt).where(
            InvoiceProcessingAttempt.id == run_id).with_for_update())
        if row is None or row.status != "running" or row.lease_token != lease:
            raise ApiProblem(409, "stale_processing", "Processing lease was superseded.")
        for name, value in fields.items():
            setattr(row, name, value)
        row.updated_at = utcnow()


def _fail(factory: sessionmaker[Session], run_id: UUID, lease: UUID,
          code: str, retryable: bool) -> None:
    with factory.begin() as session:
        row = session.scalar(select(InvoiceProcessingAttempt).where(
            InvoiceProcessingAttempt.id == run_id).with_for_update())
        if row is None or row.lease_token != lease or row.status != "running":
            return
        row.status = "failed"
        row.lease_token = None
        row.retryable = retryable
        row.failure_code = code
        row.failure_detail = "Processing failed; inspect the code and retry policy."
        row.updated_at = utcnow()
        row.completed_at = utcnow()
        session.add(AuditEvent(actor_type="system", action="processing_failed",
                               entity_type="upload_attempt", entity_id=row.upload_attempt_id,
                               after_data={"code": code, "retryable": retryable}))


def _link_invoice(session: Session, row: InvoiceProcessingAttempt,
                  extraction: ExtractionResult, validation: ValidationResult,
                  reconciliation) -> UUID | None:
    upload = session.get(InvoiceUploadAttempt, row.upload_attempt_id)
    if upload is None or upload.document_id != row.document_id:
        raise ApiProblem(409, "stale_processing", "Upload attempt changed during processing.")
    supplier_ids = reconciliation.supplier_match.supplier_ids
    if len(supplier_ids) != 1:
        return None
    supplier_id = UUID(supplier_ids[0])
    number = normalize_invoice_number(extraction.invoice.invoice_number.value)
    existing = session.scalar(select(Invoice).where(
        Invoice.supplier_id == supplier_id, Invoice.invoice_number_normalized == number)) if number else None
    if existing is not None:
        document_hashes = set(session.scalars(select(InvoiceDocument.sha256)
            .join(InvoiceUploadAttempt, InvoiceUploadAttempt.document_id == InvoiceDocument.id)
            .where(InvoiceUploadAttempt.invoice_id == existing.id)))
        if document_hashes != {row.source_sha256}:
            upload.identity_status = "conflict"
            return None
        upload.invoice_id = existing.id
        upload.identity_status = "duplicate"
        return existing.id
    observed = extraction.invoice
    invoice = Invoice(supplier_id=supplier_id, invoice_number_raw=observed.invoice_number.value,
                      invoice_number_normalized=number, invoice_date=observed.invoice_date.value,
                      status="uploaded",
                      subtotal=_nonnegative(observed.printed_subtotal.value),
                      tax=_nonnegative(observed.printed_tax.value),
                      total=_nonnegative(observed.printed_total.value))
    session.add(invoice)
    session.flush()
    invoice.status = transition_invoice(InvoiceState.UPLOADED, InvoiceOperation.COMPLETE_EXTRACTION).value
    invoice.status = transition_invoice(InvoiceState.EXTRACTED,
        InvoiceOperation.VALIDATION_PASSED if validation.decision == ReviewDecision.READY_FOR_PO_MATCHING
        else InvoiceOperation.VALIDATION_REQUIRES_REVIEW).value
    product_matches = {item.source_row_order: item for item in reconciliation.product_matches}
    for index, line in enumerate(observed.lines, 1):
        match = product_matches.get(line.source_row_order)
        product_id = UUID(match.product_ids[0]) if match and len(match.product_ids) == 1 else None
        session.add(InvoiceLine(invoice_id=invoice.id, line_number=index,
            description=line.description.value or "Unreadable product description",
            product_id=product_id, upc_raw=line.upc.value, ndc_raw=line.ndc.value,
            quantity=line.quantity.value if line.quantity.value and line.quantity.value > 0 else None,
            unit_cost=_nonnegative(line.unit_cost.value),
            line_total=_nonnegative(line.printed_line_total.value)))
    upload.invoice_id = invoice.id
    upload.identity_status = "linked"
    session.add(AuditEvent(actor_type="system", action="process",
                           entity_type="invoice", entity_id=invoice.id,
                           after_data={"status": invoice.status, "source_sha256": row.source_sha256,
                                       "processing_id": str(row.id)}))
    session.add(InvoiceReviewState(invoice_id=invoice.id, processing_id=row.id, revision=1,
        current_extraction_json=extraction.model_dump(mode="json"),
        validation_json=validation.model_dump(mode="json"),
        reconciliation_json=reconciliation.model_dump(mode="json"), corrected_fields_json=[]))
    return invoice.id


def _nonnegative(value):
    return value if value is not None and value >= 0 else None


def process_upload(factory: sessionmaker[Session], storage: LocalPdfStorage,
                   settings: ApiSettings, upload_id: UUID, *, retry_failed: bool = False) -> dict:
    config_hash = config_fingerprint(settings)
    provider = _provider(settings)
    lease = uuid4()
    with factory.begin() as session:
        upload = session.get(InvoiceUploadAttempt, upload_id)
        if upload is None:
            raise ApiProblem(404, "upload_not_found", "Upload attempt does not exist.")
        document = session.get(InvoiceDocument, upload.document_id)
        lock_key = int.from_bytes(hashlib.sha256(
            f"process:{document.sha256}:{config_hash}".encode()).digest()[:8],
            "big", signed=True)
        session.execute(select(func.pg_advisory_xact_lock(lock_key)))
        if upload.invoice_id is not None:
            review = session.get(InvoiceReviewState, upload.invoice_id)
            if review is not None and review.corrected_fields_json:
                existing_run = session.get(InvoiceProcessingAttempt, review.processing_id)
                if existing_run.config_fingerprint != config_hash:
                    raise ApiProblem(409, "stale_processing",
                                     "Staff corrections require a new review workflow, not silent reprocessing.")
        prior = session.scalar(select(InvoiceProcessingAttempt).where(
            InvoiceProcessingAttempt.document_id == document.id,
            InvoiceProcessingAttempt.config_fingerprint == config_hash,
            InvoiceProcessingAttempt.status == "completed").order_by(InvoiceProcessingAttempt.completed_at).limit(1))
        if prior is not None:
            if upload.invoice_id is None and prior.invoice_id is not None:
                upload.invoice_id = prior.invoice_id
                upload.identity_status = "duplicate"
            return processing_view(prior) | {"requested_upload_attempt_id": upload_id,
                                            "upload_identity_status": upload.identity_status}
        row = session.scalar(select(InvoiceProcessingAttempt).where(
            InvoiceProcessingAttempt.document_id == document.id,
            InvoiceProcessingAttempt.config_fingerprint == config_hash).with_for_update())
        if row is not None:
            if row.status == "running" and row.updated_at > utcnow() - timedelta(seconds=settings.processing_lease_seconds):
                raise ApiProblem(409, "processing_in_progress", "Processing is already running.")
            if row.status == "failed" and not (row.retryable or retry_failed):
                raise ApiProblem(409, "terminal_processing_failure", "Explicit retry is required.")
            if row.invoice_id is not None and session.get(InvoiceReviewState, row.invoice_id) is not None:
                raise ApiProblem(409, "stale_processing", "Staff review exists; processing cannot overwrite it.")
            row.status = "running"
            row.lease_token = lease
            row.attempt_count += 1
            row.retryable = None
            row.failure_code = row.failure_detail = None
            row.updated_at = utcnow()
            row.completed_at = None
        else:
            row = InvoiceProcessingAttempt(document_id=document.id, upload_attempt_id=upload.id,
                config_fingerprint=config_hash, source_sha256=document.sha256,
                status="running", lease_token=lease, attempt_count=1, retryable=None,
                provider=provider.name, model_name=provider.model_name,
                updated_at=utcnow())
            session.add(row)
            session.flush()
        run_id, source_hash = row.id, document.sha256
        cached_ocr, cached_extraction = row.ocr_json, row.extraction_json
    try:
        pdf = storage.read(source_hash)
        ocr = OcrDocument.model_validate_json(json.dumps(cached_ocr)) if cached_ocr else extract_ocr(pdf)
        if ocr.source_sha256 != source_hash:
            raise ApiProblem(409, "source_mismatch", "OCR source does not match the stored PDF.")
        _save_stage(factory, run_id, lease, ocr_json=ocr.model_dump(mode="json"))
        extraction = (ExtractionResult.model_validate_json(json.dumps(cached_extraction)) if cached_extraction
                      else extract_invoice(ocr, provider))
        if extraction.source_sha256 != source_hash:
            raise ApiProblem(409, "source_mismatch", "Extraction source does not match the stored PDF.")
        _save_stage(factory, run_id, lease,
                    extraction_json=extraction.model_dump(mode="json"))
        validation = validate_invoice(extraction, ocr)
        with factory() as session:
            reconciliation = reconcile_invoice(extraction, validation,
                SqlAlchemyTrustedRepository(session), current_upload_attempt_id=upload_id)
        _save_stage(factory, run_id, lease,
                    validation_json=validation.model_dump(mode="json"),
                    reconciliation_json=reconciliation.model_dump(mode="json"))
        with factory.begin() as session:
            row = session.scalar(select(InvoiceProcessingAttempt).where(
                InvoiceProcessingAttempt.id == run_id).with_for_update())
            if row is None or row.lease_token != lease or row.status != "running":
                raise ApiProblem(409, "stale_processing", "Processing lease was superseded.")
            upload = session.get(InvoiceUploadAttempt, upload_id)
            if upload.invoice_id is not None and session.get(InvoiceReviewState, upload.invoice_id) is not None:
                raise ApiProblem(409, "stale_processing", "Staff review changed during processing.")
            row.invoice_id = _link_invoice(session, row, extraction, validation, reconciliation)
            row.status = "completed"
            row.lease_token = None
            row.retryable = False
            row.completed_at = utcnow()
            row.updated_at = utcnow()
            return processing_view(row) | {"requested_upload_attempt_id": upload_id,
                                           "upload_identity_status": upload.identity_status}
    except ApiProblem as exc:
        _fail(factory, run_id, lease, exc.code, retryable=False)
        raise
    except (OcrError, ExtractionError, StorageError, ValueError) as exc:
        retryable = bool(isinstance(exc, ProviderError) and exc.retryable)
        code = ("ocr_failed" if isinstance(exc, OcrError) else
                "provider_failed" if isinstance(exc, ProviderError) else
                "storage_failed" if isinstance(exc, StorageError) else "extraction_failed")
        _fail(factory, run_id, lease, code, retryable)
        raise ApiProblem(409 if not retryable else 503, code,
                         "Processing failed; retry only after resolving the cause.") from exc
    except IntegrityError as exc:
        _fail(factory, run_id, lease, "identity_conflict", retryable=False)
        raise ApiProblem(409, "identity_conflict", "Invoice identity changed during processing.") from exc
    except Exception as exc:
        _fail(factory, run_id, lease, "processing_error", retryable=True)
        raise ApiProblem(500, "processing_error", "Processing failed before completion.") from exc
