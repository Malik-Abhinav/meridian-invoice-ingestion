"""FastAPI application factory; importing this module never connects to PostgreSQL."""

import hmac
import json
import logging
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from meridian.api.config import ApiSettings
from meridian.api.body_limit import UploadBodyLimit
from meridian.api.errors import ApiProblem
from meridian.api.processing import process_upload, processing_view
from meridian.api.review import (ApprovalRequest, CorrectionRequest, RejectRequest,
                                 apply_correction, approve_invoice, reject_invoice)
from meridian.api.schemas import (AuditListResponse, HealthResponse, InvoiceDetailResponse,
    InvoiceListResponse, MovementListResponse, PostingResponse, ProcessingListResponse,
    ReviewResponse, UploadProcessingResponse, UploadResponse)
from meridian.api.storage import LocalPdfStorage, StorageError
from meridian.db import database_url
from meridian.inventory_posting import (LineAllocation, PostingConflict, PostingErrorCode,
                                        PostingRequest, post_invoice)
from meridian.models import (AuditEvent, InventoryMovement, Invoice, InvoiceDocument,
                             InvoiceLine, InvoicePostingAuthorization, InvoiceProcessingAttempt,
                             InvoiceReviewState, InvoiceUploadAttempt)

logger = logging.getLogger("meridian.api")


class PostBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


def create_app(settings: ApiSettings | None = None,
               session_factory: sessionmaker[Session] | None = None) -> FastAPI:
    settings = settings or ApiSettings.from_env()
    app = FastAPI(title="Meridian Supplier Invoice API", version="1.0.0")
    app.add_middleware(UploadBodyLimit, max_upload_bytes=settings.max_upload_bytes)
    app.state.settings = settings
    app.state.factory = session_factory
    app.state.storage = LocalPdfStorage(settings.storage_root)
    app.state.staff_authenticator = None

    def factory(request: Request) -> sessionmaker[Session]:
        if request.app.state.factory is None:
            try:
                engine = create_engine(database_url(), pool_pre_ping=True)
            except (RuntimeError, SQLAlchemyError) as exc:
                raise ApiProblem(503, "not_ready", "Database configuration is unavailable.") from exc
            request.app.state.factory = sessionmaker(engine, expire_on_commit=False)
        return request.app.state.factory

    bearer = HTTPBearer(auto_error=False)

    def staff(request: Request,
              credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
        if credentials is None:
            raise ApiProblem(401, "authentication_required", "A staff bearer token is required.")
        token = credentials.credentials
        if settings.dev_auth_enabled:
            if not hmac.compare_digest(token, settings.dev_staff_token or ""):
                raise ApiProblem(401, "invalid_credentials", "Staff credentials are invalid.")
            if (settings.dev_token_expires_at is not None
                    and datetime.now(timezone.utc) >= settings.dev_token_expires_at):
                raise ApiProblem(401, "credentials_expired", "Staff credentials have expired.")
            return settings.dev_staff_id
        verifier = request.app.state.staff_authenticator
        if verifier is None:
            raise ApiProblem(401, "authentication_unavailable", "Staff authentication is not configured.")
        actor = verifier(token)
        if not isinstance(actor, str) or not actor.strip():
            raise ApiProblem(403, "staff_forbidden", "Staff access is required.")
        return actor

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        supplied = request.headers.get("x-correlation-id", "")
        request.state.correlation_id = supplied if 1 <= len(supplied) <= 80 and supplied.isascii() and supplied.isprintable() else str(uuid4())
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        logger.info("request %s %s %s", request.state.correlation_id, request.method, request.url.path)
        return response

    @app.exception_handler(ApiProblem)
    async def api_problem(request: Request, exc: ApiProblem):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code,
            "message": str(exc), "correlation_id": request.state.correlation_id}})

    @app.exception_handler(StorageError)
    async def storage_problem(request: Request, exc: StorageError):
        status = 413 if exc.code == "upload_too_large" else 400 if exc.code in ("invalid_pdf", "invalid_filename") else 500
        return JSONResponse(status_code=status, content={"error": {"code": exc.code,
            "message": str(exc), "correlation_id": request.state.correlation_id}})

    @app.exception_handler(RequestValidationError)
    async def validation_problem(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {"code": "invalid_request",
            "message": "Request fields are invalid.", "correlation_id": request.state.correlation_id,
            "details": [{"field": ".".join(str(part) for part in item["loc"]),
                         "code": item["type"]} for item in exc.errors()]}})

    @app.exception_handler(StarletteHTTPException)
    async def http_problem(request: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": {
            "code": "not_found" if exc.status_code == 404 else "http_error",
            "message": "Requested endpoint is unavailable.",
            "correlation_id": request.state.correlation_id}})

    @app.exception_handler(PostingConflict)
    async def posting_problem(request: Request, exc: PostingConflict):
        status = (404 if exc.code == PostingErrorCode.INVOICE_NOT_FOUND else
                  503 if exc.code == PostingErrorCode.TRANSACTION_CONFLICT else 409)
        return JSONResponse(status_code=status, content={"error": {"code": exc.code.value,
            "message": str(exc), "correlation_id": request.state.correlation_id}})

    @app.exception_handler(Exception)
    async def internal_problem(request: Request, exc: Exception):
        logger.error("request %s failed with %s", request.state.correlation_id,
                     type(exc).__name__)
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error",
            "message": "Request could not be completed.",
            "correlation_id": request.state.correlation_id}})

    @app.get("/health", response_model=HealthResponse)
    def health():
        return {"status": "ok"}

    @app.get("/ready", response_model=HealthResponse)
    def ready(db: sessionmaker[Session] = Depends(factory)):
        try:
            with db() as session:
                session.execute(text("SELECT 1"))
            settings.storage_root.mkdir(parents=True, exist_ok=True)
            if not settings.storage_root.is_dir():
                raise OSError("storage root unavailable")
            if settings.provider == "openai" and not os.getenv("OPENAI_API_KEY"):
                raise OSError("configured provider unavailable")
        except (SQLAlchemyError, OSError, RuntimeError) as exc:
            raise ApiProblem(503, "not_ready", "Required database or storage dependency is unavailable.") from exc
        return {"status": "ready"}

    @app.post("/api/v1/invoices/upload", status_code=201, response_model=UploadResponse)
    async def upload_invoice(request: Request, file: UploadFile = File(...),
                             _: str = Depends(staff), db: sessionmaker[Session] = Depends(factory)):
        digest, size, _ = await app.state.storage.store(file,
            max_bytes=settings.max_upload_bytes, max_pages=settings.max_pdf_pages)
        try:
            with db.begin() as session:
                stmt = pg_insert(InvoiceDocument).values(sha256=digest,
                    storage_uri=f"sha256:{digest}", content_type="application/pdf")
                session.execute(stmt.on_conflict_do_nothing(index_elements=[InvoiceDocument.sha256]))
                document = session.scalar(select(InvoiceDocument).where(InvoiceDocument.sha256 == digest))
                prior = session.scalar(select(InvoiceUploadAttempt).where(
                    InvoiceUploadAttempt.document_id == document.id,
                    InvoiceUploadAttempt.invoice_id.is_not(None)).order_by(
                    InvoiceUploadAttempt.uploaded_at, InvoiceUploadAttempt.id).limit(1))
                attempt = InvoiceUploadAttempt(document_id=document.id,
                    invoice_id=prior.invoice_id if prior else None,
                    original_filename=file.filename, identity_status="duplicate" if prior else "pending")
                session.add(attempt)
                session.flush()
                return {"document_id": document.id, "upload_attempt_id": attempt.id,
                        "invoice_id": attempt.invoice_id, "identity_status": attempt.identity_status,
                        "source_sha256": digest, "size_bytes": size,
                        "correlation_id": request.state.correlation_id}
        except IntegrityError as exc:
            raise ApiProblem(409, "upload_conflict", "Concurrent document upload could not be linked.") from exc

    @app.post("/api/v1/uploads/{upload_id}/process")
    def process_upload_route(upload_id: UUID, retry_failed: bool = False,
                             _: str = Depends(staff), db: sessionmaker[Session] = Depends(factory)):
        return process_upload(db, app.state.storage, settings, upload_id, retry_failed=retry_failed)

    @app.get("/api/v1/uploads/{upload_id}/processing", response_model=UploadProcessingResponse)
    def upload_processing(upload_id: UUID, _: str = Depends(staff),
                          db: sessionmaker[Session] = Depends(factory)):
        with db() as session:
            if session.get(InvoiceUploadAttempt, upload_id) is None:
                raise ApiProblem(404, "upload_not_found", "Upload attempt does not exist.")
            upload = session.get(InvoiceUploadAttempt, upload_id)
            return {"identity_status": upload.identity_status,
                    "items": [processing_view(row) for row in session.scalars(select(InvoiceProcessingAttempt)
                .where(InvoiceProcessingAttempt.document_id == upload.document_id)
                .order_by(InvoiceProcessingAttempt.started_at, InvoiceProcessingAttempt.id))]}

    @app.post("/api/v1/invoices/{invoice_id}/process")
    def process_invoice_route(invoice_id: UUID, retry_failed: bool = False,
                              _: str = Depends(staff), db: sessionmaker[Session] = Depends(factory)):
        with db() as session:
            attempt = session.scalar(select(InvoiceUploadAttempt).where(
                InvoiceUploadAttempt.invoice_id == invoice_id).order_by(
                InvoiceUploadAttempt.uploaded_at.desc(), InvoiceUploadAttempt.id.desc()).limit(1))
            if attempt is None:
                raise ApiProblem(404, "invoice_not_found", "Invoice or linked upload does not exist.")
            upload_id = attempt.id
        return process_upload(db, app.state.storage, settings, upload_id, retry_failed=retry_failed)

    def invoice_detail(session: Session, invoice: Invoice) -> dict:
        review = session.get(InvoiceReviewState, invoice.id)
        lines = list(session.scalars(select(InvoiceLine).where(
            InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.line_number)))
        uploads = list(session.scalars(select(InvoiceUploadAttempt).where(
            InvoiceUploadAttempt.invoice_id == invoice.id).order_by(
            InvoiceUploadAttempt.uploaded_at, InvoiceUploadAttempt.id)))
        authorization = session.scalar(select(InvoicePostingAuthorization).where(
            InvoicePostingAuthorization.invoice_id == invoice.id))
        return {"id": invoice.id, "status": invoice.status, "supplier_id": invoice.supplier_id,
                "invoice_number": invoice.invoice_number_raw,
                "invoice_number_normalized": invoice.invoice_number_normalized,
                "invoice_date": invoice.invoice_date, "subtotal": str(invoice.subtotal) if invoice.subtotal is not None else None,
                "tax": str(invoice.tax) if invoice.tax is not None else None,
                "total": str(invoice.total) if invoice.total is not None else None,
                "lines": [{"id": line.id, "line_number": line.line_number,
                    "product_id": line.product_id, "description": line.description,
                    "upc": line.upc_raw, "ndc": line.ndc_raw,
                    "quantity": line.quantity,
                    "unit_cost": str(line.unit_cost) if line.unit_cost is not None else None,
                    "line_total": str(line.line_total) if line.line_total is not None else None} for line in lines],
                "upload_attempt_ids": [item.id for item in uploads],
                "source_document": None if not uploads else {
                    "id": uploads[0].document_id,
                    "sha256": session.get(InvoiceDocument, uploads[0].document_id).sha256},
                "processing_status": None if review is None else session.get(InvoiceProcessingAttempt, review.processing_id).status,
                "revision": None if review is None else review.revision,
                "validation": None if review is None else review.validation_json,
                "reconciliation": None if review is None else review.reconciliation_json,
                "posting_status": None if authorization is None else ("posted" if authorization.posted_at else "authorized")}

    @app.get("/api/v1/invoices", response_model=InvoiceListResponse)
    def list_invoices(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                      status: str | None = None, supplier_id: UUID | None = None,
                      _: str = Depends(staff), db: sessionmaker[Session] = Depends(factory)):
        with db() as session:
            query = select(Invoice)
            if status is not None:
                query = query.where(Invoice.status == status)
            if supplier_id is not None:
                query = query.where(Invoice.supplier_id == supplier_id)
            total = session.scalar(select(func.count()).select_from(query.subquery()))
            rows = session.scalars(query.order_by(Invoice.created_at, Invoice.id).limit(limit).offset(offset))
            return {"items": [invoice_detail(session, invoice) for invoice in rows],
                    "pagination": {"limit": limit, "offset": offset, "total": total}}

    @app.get("/api/v1/invoices/{invoice_id}", response_model=InvoiceDetailResponse)
    def get_invoice(invoice_id: UUID, _: str = Depends(staff),
                    db: sessionmaker[Session] = Depends(factory)):
        with db() as session:
            invoice = session.get(Invoice, invoice_id)
            if invoice is None:
                raise ApiProblem(404, "invoice_not_found", "Invoice does not exist.")
            return invoice_detail(session, invoice)

    @app.get("/api/v1/invoices/{invoice_id}/processing", response_model=ProcessingListResponse)
    def invoice_processing(invoice_id: UUID, _: str = Depends(staff),
                           db: sessionmaker[Session] = Depends(factory)):
        with db() as session:
            if session.get(Invoice, invoice_id) is None:
                raise ApiProblem(404, "invoice_not_found", "Invoice does not exist.")
            rows = session.scalars(select(InvoiceProcessingAttempt).where(
                InvoiceProcessingAttempt.invoice_id == invoice_id).order_by(
                InvoiceProcessingAttempt.started_at, InvoiceProcessingAttempt.id))
            return {"items": [processing_view(row) for row in rows]}

    @app.get("/api/v1/invoices/{invoice_id}/audit", response_model=AuditListResponse)
    def invoice_audit(invoice_id: UUID, _: str = Depends(staff),
                      db: sessionmaker[Session] = Depends(factory)):
        with db() as session:
            if session.get(Invoice, invoice_id) is None:
                raise ApiProblem(404, "invoice_not_found", "Invoice does not exist.")
            rows = session.scalars(select(AuditEvent).where(AuditEvent.entity_type == "invoice",
                AuditEvent.entity_id == invoice_id).order_by(AuditEvent.created_at, AuditEvent.id))
            return {"items": [{"id": row.id, "actor_type": row.actor_type,
                "actor_id": row.actor_id, "action": row.action, "before": row.before_data,
                "after": row.after_data, "created_at": row.created_at} for row in rows]}

    @app.get("/api/v1/invoices/{invoice_id}/movements", response_model=MovementListResponse)
    def invoice_movements(invoice_id: UUID, _: str = Depends(staff),
                          db: sessionmaker[Session] = Depends(factory)):
        with db() as session:
            if session.get(Invoice, invoice_id) is None:
                raise ApiProblem(404, "invoice_not_found", "Invoice does not exist.")
            rows = session.scalars(select(InventoryMovement).join(
                InvoiceLine, InventoryMovement.invoice_line_id == InvoiceLine.id).where(
                InvoiceLine.invoice_id == invoice_id).order_by(
                InventoryMovement.created_at, InventoryMovement.id))
            return {"items": [{"id": row.id, "movement_type": row.movement_type,
                "invoice_line_id": row.invoice_line_id,
                "purchase_order_line_id": row.purchase_order_line_id,
                "product_id": row.product_id, "location_id": row.location_id,
                "quantity_delta": row.quantity_delta,
                "unit_cost": str(row.unit_cost) if row.unit_cost is not None else None,
                "posting_key": row.posting_key, "created_at": row.created_at}
                for row in rows]}

    @app.get("/api/v1/invoices/{invoice_id}/document")
    def invoice_document(invoice_id: UUID, _: str = Depends(staff),
                         db: sessionmaker[Session] = Depends(factory)):
        with db() as session:
            review = session.get(InvoiceReviewState, invoice_id)
            if review is None:
                raise ApiProblem(404, "document_not_found", "Invoice has no linked source document.")
            run = session.get(InvoiceProcessingAttempt, review.processing_id)
            digest = run.source_sha256
        return Response(content=app.state.storage.read(digest), media_type="application/pdf",
                        headers={"Content-Disposition": 'attachment; filename="invoice.pdf"',
                                 "X-Content-Type-Options": "nosniff"})

    @app.get("/api/v1/invoices/{invoice_id}/review", response_model=ReviewResponse)
    def review_invoice(invoice_id: UUID, _: str = Depends(staff),
                       db: sessionmaker[Session] = Depends(factory)):
        with db() as session:
            invoice = session.get(Invoice, invoice_id)
            review = session.get(InvoiceReviewState, invoice_id)
            if invoice is None or review is None:
                raise ApiProblem(404, "invoice_not_found", "Invoice review does not exist.")
            run = session.get(InvoiceProcessingAttempt, review.processing_id)
            corrected = set(review.corrected_fields_json)
            def local_reviewed(finding: dict) -> bool:
                if not finding.get("manual_review_required", True):
                    return True
                if finding["code"] == "duplicate_product_identifier":
                    return True
                field = finding["field"]
                reference = (f"line[{field['source_row_order']}].{field['field']}"
                             if field.get("source_row_order") is not None else field["field"])
                return (finding["code"] in {"missing_evidence", "conflicting_evidence",
                    "low_ocr_confidence", "uncertain_field"} and reference in corrected)
            return {"invoice": invoice_detail(session, invoice),
                    "original_extraction": run.extraction_json,
                    "current_extraction": review.current_extraction_json,
                    "ocr": run.ocr_json, "validation": review.validation_json,
                    "reconciliation": review.reconciliation_json,
                    "corrected_fields": review.corrected_fields_json,
                    "allocation_candidates": [{"source_row_order": row["source_row_order"],
                        "po_line_ids": row["po_line_ids"]} for row in
                        review.reconciliation_json.get("product_matches", [])],
                    "authorization_ready": invoice.status in ("validated", "review_required")
                        and all(local_reviewed(item) for item in review.validation_json.get("findings", []))
                        and not any(item.get("manual_review_required", True)
                                    for item in review.reconciliation_json.get("findings", []))
                        and not review.reconciliation_json.get("unperformed_checks")}

    @app.patch("/api/v1/invoices/{invoice_id}")
    def correct_invoice(invoice_id: UUID, body: CorrectionRequest,
                        actor: str = Depends(staff), db: sessionmaker[Session] = Depends(factory)):
        return apply_correction(db, invoice_id, body, actor)

    @app.post("/api/v1/invoices/{invoice_id}/approve")
    def approve(invoice_id: UUID, body: ApprovalRequest,
                actor: str = Depends(staff), db: sessionmaker[Session] = Depends(factory)):
        return approve_invoice(db, invoice_id, body, actor)

    @app.post("/api/v1/invoices/{invoice_id}/reject")
    def reject(invoice_id: UUID, body: RejectRequest,
               actor: str = Depends(staff), db: sessionmaker[Session] = Depends(factory)):
        return reject_invoice(db, invoice_id, body, actor)

    @app.post("/api/v1/invoices/{invoice_id}/post", response_model=PostingResponse)
    def post(invoice_id: UUID, body: PostBody, actor: str = Depends(staff),
             db: sessionmaker[Session] = Depends(factory)):
        with db() as session:
            if session.get(Invoice, invoice_id) is None:
                raise ApiProblem(404, "invoice_not_found", "Invoice does not exist.")
            authorization = session.scalar(select(InvoicePostingAuthorization).where(
                InvoicePostingAuthorization.invoice_id == invoice_id))
            review = session.get(InvoiceReviewState, invoice_id)
            if authorization is None or review is None or not review.approved_allocations_json:
                raise ApiProblem(409, "missing_approval_evidence", "Invoice has no current posting authorization.")
            posting = PostingRequest(invoice_id=invoice_id, authorization_id=authorization.id,
                location_id=authorization.location_id, idempotency_key=body.idempotency_key,
                allocations=tuple(LineAllocation.model_validate_json(json.dumps(item))
                                  for item in review.approved_allocations_json))
        result = post_invoice(db, posting, actor_id=actor)
        return result.model_dump(mode="json")

    return app


app = create_app()
