"""Persistence schema for supplier invoices and their upload history."""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from meridian.db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    invoices: Mapped[list["Invoice"]] = relationship(back_populates="supplier")
    purchase_orders: Mapped[list["PurchaseOrder"]] = relationship(back_populates="supplier")

    __table_args__ = (
        CheckConstraint("length(trim(code)) > 0", name="ck_suppliers_code_not_blank"),
        CheckConstraint("length(trim(name)) > 0", name="ck_suppliers_name_not_blank"),
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    upc_normalized: Mapped[str | None] = mapped_column(String(20))
    ndc_normalized: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    invoice_lines: Mapped[list["InvoiceLine"]] = relationship(back_populates="product")
    purchase_order_lines: Mapped[list["PurchaseOrderLine"]] = relationship(back_populates="product")
    inventory_balances: Mapped[list["InventoryBalance"]] = relationship(back_populates="product")
    inventory_movements: Mapped[list["InventoryMovement"]] = relationship(back_populates="product")

    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_products_name_not_blank"),
        CheckConstraint("upc_normalized IS NOT NULL OR ndc_normalized IS NOT NULL", name="ck_products_has_identifier"),
        CheckConstraint("upc_normalized IS NULL OR length(trim(upc_normalized)) > 0", name="ck_products_upc_not_blank"),
        CheckConstraint("ndc_normalized IS NULL OR length(trim(ndc_normalized)) > 0", name="ck_products_ndc_not_blank"),
        Index("uq_products_upc_normalized", "upc_normalized", unique=True, postgresql_where=text("upc_normalized IS NOT NULL")),
        Index("uq_products_ndc_normalized", "ndc_normalized", unique=True, postgresql_where=text("ndc_normalized IS NOT NULL")),
    )


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    supplier_id: Mapped[UUID] = mapped_column(ForeignKey("suppliers.id", ondelete="RESTRICT"))
    invoice_number_raw: Mapped[str | None] = mapped_column(String(120))
    invoice_number_normalized: Mapped[str | None] = mapped_column(String(120))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(24))
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(18, 4, asdecimal=True))
    tax: Mapped[Decimal | None] = mapped_column(Numeric(18, 4, asdecimal=True))
    total: Mapped[Decimal | None] = mapped_column(Numeric(18, 4, asdecimal=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    supplier: Mapped[Supplier] = relationship(back_populates="invoices")
    lines: Mapped[list["InvoiceLine"]] = relationship(back_populates="invoice")
    upload_attempts: Mapped[list["InvoiceUploadAttempt"]] = relationship(back_populates="invoice")

    __table_args__ = (
        CheckConstraint("invoice_number_normalized IS NULL OR length(trim(invoice_number_normalized)) > 0", name="ck_invoices_number_not_blank"),
        CheckConstraint("status IN ('uploaded', 'extracted', 'review_required', 'validated', 'approved', 'posted', 'rejected', 'failed')", name="ck_invoices_status"),
        CheckConstraint("subtotal IS NULL OR subtotal >= 0", name="ck_invoices_subtotal_nonnegative"),
        CheckConstraint("tax IS NULL OR tax >= 0", name="ck_invoices_tax_nonnegative"),
        CheckConstraint("total IS NULL OR total >= 0", name="ck_invoices_total_nonnegative"),
        Index("uq_invoices_supplier_number", "supplier_id", "invoice_number_normalized", unique=True, postgresql_where=text("invoice_number_normalized IS NOT NULL")),
        Index("ix_invoices_status_created", "status", "created_at"),
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    invoice_id: Mapped[UUID] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"))
    line_number: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(500))
    product_id: Mapped[UUID | None] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    upc_raw: Mapped[str | None] = mapped_column(String(80))
    ndc_raw: Mapped[str | None] = mapped_column(String(80))
    quantity: Mapped[int | None] = mapped_column(Integer)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4, asdecimal=True))
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 4, asdecimal=True))

    invoice: Mapped[Invoice] = relationship(back_populates="lines")
    product: Mapped[Product | None] = relationship(back_populates="invoice_lines")
    receipt_movements: Mapped[list["InventoryMovement"]] = relationship(back_populates="invoice_line")

    __table_args__ = (
        CheckConstraint("line_number > 0", name="ck_invoice_lines_number_positive"),
        CheckConstraint("length(trim(description)) > 0", name="ck_invoice_lines_description_not_blank"),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_invoice_lines_quantity_positive"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="ck_invoice_lines_unit_cost_nonnegative"),
        CheckConstraint("line_total IS NULL OR line_total >= 0", name="ck_invoice_lines_total_nonnegative"),
        Index("uq_invoice_lines_invoice_number", "invoice_id", "line_number", unique=True),
        Index("ix_invoice_lines_product_id", "product_id"),
    )


class InvoiceDocument(Base):
    __tablename__ = "invoice_documents"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    storage_uri: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    upload_attempts: Mapped[list["InvoiceUploadAttempt"]] = relationship(back_populates="document")

    __table_args__ = (
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_invoice_documents_sha256_hex"),
        CheckConstraint("length(trim(storage_uri)) > 0", name="ck_invoice_documents_uri_not_blank"),
    )


class InvoiceUploadAttempt(Base):
    __tablename__ = "invoice_upload_attempts"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("invoice_documents.id", ondelete="RESTRICT"))
    invoice_id: Mapped[UUID | None] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"))
    original_filename: Mapped[str] = mapped_column(String(255))
    identity_status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    document: Mapped[InvoiceDocument] = relationship(back_populates="upload_attempts")
    invoice: Mapped[Invoice | None] = relationship(back_populates="upload_attempts")

    __table_args__ = (
        CheckConstraint("identity_status IN ('pending', 'linked', 'duplicate', 'conflict', 'missing_number')", name="ck_upload_attempts_identity_status"),
        CheckConstraint("identity_status <> 'linked' OR invoice_id IS NOT NULL", name="ck_upload_attempts_linked_invoice"),
        Index("ix_upload_attempts_document_uploaded", "document_id", "uploaded_at"),
        Index("ix_upload_attempts_invoice_uploaded", "invoice_id", "uploaded_at"),
    )
