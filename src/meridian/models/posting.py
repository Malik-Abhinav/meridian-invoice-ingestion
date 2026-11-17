"""One auditable authorization and one completed posting per invoice."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from meridian.db import Base
from meridian.models.invoices import utc_now


class InvoicePostingAuthorization(Base):
    __tablename__ = "invoice_posting_authorizations"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    invoice_id: Mapped[UUID] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), unique=True)
    approval_event_id: Mapped[UUID] = mapped_column(ForeignKey("audit_events.id", ondelete="RESTRICT"), unique=True)
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    source_sha256: Mapped[str] = mapped_column(String(64))
    invoice_fingerprint: Mapped[str] = mapped_column(String(64))
    reference_fingerprint: Mapped[str] = mapped_column(String(64))
    allocation_fingerprint: Mapped[str] = mapped_column(String(64))
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    approval_payload_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    __table_args__ = (
        CheckConstraint("(idempotency_key IS NULL AND request_fingerprint IS NULL AND posted_at IS NULL) OR "
                        "(idempotency_key IS NOT NULL AND request_fingerprint IS NOT NULL AND posted_at IS NOT NULL)",
                        name="ck_posting_authorizations_completion"),
        CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_posting_authorizations_source_hash"),
    )
