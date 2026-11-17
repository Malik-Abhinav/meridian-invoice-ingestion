"""Durable processing attempts and staff-corrected review snapshots.

Revision ID: 0003_api_processing
Revises: 0002_posting_authorization
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_api_processing"
down_revision: Union[str, Sequence[str], None] = "0002_posting_authorization"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoice_processing_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("upload_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=True),
        sa.Column("config_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_name", sa.String(120), nullable=False),
        sa.Column("ocr_json", postgresql.JSONB(), nullable=True),
        sa.Column("extraction_json", postgresql.JSONB(), nullable=True),
        sa.Column("validation_json", postgresql.JSONB(), nullable=True),
        sa.Column("reconciliation_json", postgresql.JSONB(), nullable=True),
        sa.Column("failure_code", sa.String(80), nullable=True),
        sa.Column("failure_detail", sa.String(500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_processing_attempts_status"),
        sa.CheckConstraint("attempt_count > 0", name="ck_processing_attempts_count_positive"),
        sa.CheckConstraint("(status = 'running' AND lease_token IS NOT NULL) OR "
                           "(status <> 'running' AND lease_token IS NULL)", name="ck_processing_attempts_lease"),
        sa.ForeignKeyConstraint(["document_id"], ["invoice_documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["upload_attempt_id"], ["invoice_upload_attempts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_processing_document_config", "invoice_processing_attempts",
                    ["document_id", "config_fingerprint"], unique=True)
    op.create_index("ix_processing_invoice_started", "invoice_processing_attempts",
                    ["invoice_id", "started_at"])
    op.create_table(
        "invoice_review_states",
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("processing_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("current_extraction_json", postgresql.JSONB(), nullable=False),
        sa.Column("validation_json", postgresql.JSONB(), nullable=False),
        sa.Column("reconciliation_json", postgresql.JSONB(), nullable=False),
        sa.Column("corrected_fields_json", postgresql.JSONB(), nullable=False),
        sa.Column("approved_allocations_json", postgresql.JSONB(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_review_states_revision_positive"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["processing_id"], ["invoice_processing_attempts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("invoice_id"),
    )


def downgrade() -> None:
    op.drop_table("invoice_review_states")
    op.drop_index("ix_processing_invoice_started", table_name="invoice_processing_attempts")
    op.drop_index("uq_processing_document_config", table_name="invoice_processing_attempts")
    op.drop_table("invoice_processing_attempts")
