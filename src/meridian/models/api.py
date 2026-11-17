"""Durable HTTP processing and staff correction state; source observations stay immutable."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from meridian.db import Base


class InvoiceProcessingAttempt(Base):
    __tablename__ = "invoice_processing_attempts"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("invoice_documents.id", ondelete="RESTRICT"))
    upload_attempt_id: Mapped[UUID] = mapped_column(ForeignKey("invoice_upload_attempts.id", ondelete="RESTRICT"))
    invoice_id: Mapped[UUID | None] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"))
    config_fingerprint: Mapped[str] = mapped_column(String(64))
    source_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="running")
    lease_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    retryable: Mapped[bool | None]
    provider: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(String(120))
    ocr_json: Mapped[dict | None] = mapped_column(JSONB)
    extraction_json: Mapped[dict | None] = mapped_column(JSONB)
    validation_json: Mapped[dict | None] = mapped_column(JSONB)
    reconciliation_json: Mapped[dict | None] = mapped_column(JSONB)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_detail: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_processing_attempts_status"),
        CheckConstraint("attempt_count > 0", name="ck_processing_attempts_count_positive"),
        CheckConstraint("(status = 'running' AND lease_token IS NOT NULL) OR "
                        "(status <> 'running' AND lease_token IS NULL)",
                        name="ck_processing_attempts_lease"),
        Index("uq_processing_document_config", "document_id", "config_fingerprint", unique=True),
        Index("ix_processing_invoice_started", "invoice_id", "started_at"),
    )


class InvoiceReviewState(Base):
    __tablename__ = "invoice_review_states"

    invoice_id: Mapped[UUID] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), primary_key=True)
    processing_id: Mapped[UUID] = mapped_column(ForeignKey("invoice_processing_attempts.id", ondelete="RESTRICT"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    current_extraction_json: Mapped[dict] = mapped_column(JSONB)
    validation_json: Mapped[dict] = mapped_column(JSONB)
    reconciliation_json: Mapped[dict] = mapped_column(JSONB)
    corrected_fields_json: Mapped[list] = mapped_column(JSONB, default=list)
    approved_allocations_json: Mapped[list | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_review_states_revision_positive"),
    )
