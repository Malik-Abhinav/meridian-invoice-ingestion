"""Stable public response contracts for invoice lifecycle and staff review."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: Literal["ok", "ready"]


class UploadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: UUID
    upload_attempt_id: UUID
    invoice_id: UUID | None
    identity_status: Literal["pending", "duplicate"]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    correlation_id: str


class PostingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["posted", "already_posted"]
    invoice_id: UUID
    authorization_id: UUID
    idempotency_key: str
    movement_ids: tuple[UUID, ...]


class InvoiceLineResponse(BaseModel):
    id: UUID
    line_number: int
    product_id: UUID | None
    description: str
    upc: str | None
    ndc: str | None
    quantity: int | None
    unit_cost: str | None
    line_total: str | None


class SourceDocumentResponse(BaseModel):
    id: UUID
    sha256: str


class InvoiceDetailResponse(BaseModel):
    id: UUID
    status: str
    supplier_id: UUID
    invoice_number: str | None
    invoice_number_normalized: str | None
    invoice_date: date | None
    subtotal: str | None
    tax: str | None
    total: str | None
    lines: list[InvoiceLineResponse]
    upload_attempt_ids: list[UUID]
    source_document: SourceDocumentResponse | None
    processing_status: str | None
    revision: int | None
    validation: dict[str, Any] | None
    reconciliation: dict[str, Any] | None
    posting_status: str | None


class PaginationResponse(BaseModel):
    limit: int
    offset: int
    total: int


class InvoiceListResponse(BaseModel):
    items: list[InvoiceDetailResponse]
    pagination: PaginationResponse


class ProcessingFailureResponse(BaseModel):
    code: str
    detail: str | None


class ProcessingAttemptResponse(BaseModel):
    id: UUID
    upload_attempt_id: UUID
    document_id: UUID
    invoice_id: UUID | None
    source_sha256: str
    config_fingerprint: str
    status: str
    attempt_count: int
    retryable: bool | None
    provider: str
    model_name: str
    ocr: dict[str, Any] | None
    extraction: dict[str, Any] | None
    validation: dict[str, Any] | None
    reconciliation: dict[str, Any] | None
    failure: ProcessingFailureResponse | None
    started_at: datetime
    completed_at: datetime | None


class ProcessingListResponse(BaseModel):
    items: list[ProcessingAttemptResponse]


class UploadProcessingResponse(ProcessingListResponse):
    identity_status: str


class AuditEventResponse(BaseModel):
    id: UUID
    actor_type: str
    actor_id: str | None
    action: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    created_at: datetime


class AuditListResponse(BaseModel):
    items: list[AuditEventResponse]


class MovementResponse(BaseModel):
    id: UUID
    movement_type: str
    invoice_line_id: UUID | None
    purchase_order_line_id: UUID | None
    product_id: UUID
    location_id: UUID
    quantity_delta: int
    unit_cost: str | None
    posting_key: str
    created_at: datetime


class MovementListResponse(BaseModel):
    items: list[MovementResponse]


class AllocationCandidateResponse(BaseModel):
    source_row_order: int
    po_line_ids: list[UUID]


class ReviewResponse(BaseModel):
    invoice: InvoiceDetailResponse
    original_extraction: dict[str, Any]
    current_extraction: dict[str, Any]
    ocr: dict[str, Any]
    validation: dict[str, Any]
    reconciliation: dict[str, Any]
    corrected_fields: list[str]
    allocation_candidates: list[AllocationCandidateResponse]
    authorization_ready: bool
