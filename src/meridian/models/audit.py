"""Independent record of system and staff actions."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from meridian.db import Base
from meridian.models.invoices import utc_now


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str | None] = mapped_column(String(160))
    action: Mapped[str] = mapped_column(String(120))
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    before_data: Mapped[dict | None] = mapped_column(JSONB)
    after_data: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    __table_args__ = (
        CheckConstraint("actor_type IN ('system', 'staff')", name="ck_audit_events_actor_type"),
        CheckConstraint("actor_type <> 'staff' OR (actor_id IS NOT NULL AND length(trim(actor_id)) > 0)", name="ck_audit_events_staff_actor"),
        CheckConstraint("length(trim(action)) > 0", name="ck_audit_events_action_not_blank"),
        CheckConstraint("length(trim(entity_type)) > 0", name="ck_audit_events_entity_not_blank"),
        Index("ix_audit_events_entity_time", "entity_type", "entity_id", "created_at"),
        Index("ix_audit_events_actor_time", "actor_type", "actor_id", "created_at"),
    )
