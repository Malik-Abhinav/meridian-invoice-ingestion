"""Purchase orders and ordered lines; received quantities derive from movements."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from meridian.db import Base
from meridian.models.invoices import Product, Supplier, utc_now


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    supplier_id: Mapped[UUID] = mapped_column(ForeignKey("suppliers.id", ondelete="RESTRICT"))
    order_number: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    supplier: Mapped[Supplier] = relationship(back_populates="purchase_orders")
    lines: Mapped[list["PurchaseOrderLine"]] = relationship(back_populates="purchase_order")

    __table_args__ = (
        CheckConstraint("length(trim(order_number)) > 0", name="ck_purchase_orders_number_not_blank"),
        CheckConstraint("status IN ('draft', 'open', 'closed', 'cancelled')", name="ck_purchase_orders_status"),
        Index("uq_purchase_orders_supplier_number", "supplier_id", "order_number", unique=True),
        Index("ix_purchase_orders_supplier_status", "supplier_id", "status"),
    )


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    purchase_order_id: Mapped[UUID] = mapped_column(ForeignKey("purchase_orders.id", ondelete="RESTRICT"))
    line_number: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    ordered_quantity: Mapped[int] = mapped_column(Integer)
    expected_unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4, asdecimal=True))

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship(back_populates="purchase_order_lines")
    receipt_movements: Mapped[list["InventoryMovement"]] = relationship(back_populates="purchase_order_line")

    __table_args__ = (
        CheckConstraint("line_number > 0", name="ck_purchase_order_lines_number_positive"),
        CheckConstraint("ordered_quantity > 0", name="ck_purchase_order_lines_quantity_positive"),
        CheckConstraint("expected_unit_cost >= 0", name="ck_purchase_order_lines_cost_nonnegative"),
        Index("uq_purchase_order_lines_order_number", "purchase_order_id", "line_number", unique=True),
        Index("ix_purchase_order_lines_product_id", "product_id"),
    )
