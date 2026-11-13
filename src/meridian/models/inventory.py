"""Inventory locations, balances, and traceable quantity movements."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from meridian.db import Base
from meridian.models.invoices import InvoiceLine, Product, utc_now
from meridian.models.purchasing import PurchaseOrderLine


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    balances: Mapped[list["InventoryBalance"]] = relationship(back_populates="location")
    movements: Mapped[list["InventoryMovement"]] = relationship(back_populates="location")

    __table_args__ = (
        CheckConstraint("length(trim(code)) > 0", name="ck_locations_code_not_blank"),
        CheckConstraint("length(trim(name)) > 0", name="ck_locations_name_not_blank"),
    )


class InventoryBalance(Base):
    __tablename__ = "inventory_balances"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    available_quantity: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now(), onupdate=utc_now)

    product: Mapped[Product] = relationship(back_populates="inventory_balances")
    location: Mapped[Location] = relationship(back_populates="balances")

    __table_args__ = (
        CheckConstraint("available_quantity >= 0", name="ck_inventory_balances_nonnegative"),
        Index("uq_inventory_balances_product_location", "product_id", "location_id", unique=True),
        Index("ix_inventory_balances_location_product", "location_id", "product_id"),
    )


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    movement_type: Mapped[str] = mapped_column(String(16))
    quantity_delta: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4, asdecimal=True))
    posting_key: Mapped[str] = mapped_column(String(160), unique=True)
    invoice_line_id: Mapped[UUID | None] = mapped_column(ForeignKey("invoice_lines.id", ondelete="RESTRICT"))
    purchase_order_line_id: Mapped[UUID | None] = mapped_column(ForeignKey("purchase_order_lines.id", ondelete="RESTRICT"))
    reverses_movement_id: Mapped[UUID | None] = mapped_column(ForeignKey("inventory_movements.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())

    product: Mapped[Product] = relationship(back_populates="inventory_movements")
    location: Mapped[Location] = relationship(back_populates="movements")
    invoice_line: Mapped[InvoiceLine | None] = relationship(back_populates="receipt_movements")
    purchase_order_line: Mapped[PurchaseOrderLine | None] = relationship(back_populates="receipt_movements")
    reverses_movement: Mapped["InventoryMovement | None"] = relationship(remote_side=[id])

    __table_args__ = (
        CheckConstraint("movement_type IN ('receipt', 'adjustment', 'reversal')", name="ck_inventory_movements_type"),
        CheckConstraint("quantity_delta <> 0", name="ck_inventory_movements_nonzero"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="ck_inventory_movements_cost_nonnegative"),
        CheckConstraint("length(trim(posting_key)) > 0", name="ck_inventory_movements_key_not_blank"),
        CheckConstraint("(movement_type = 'receipt' AND quantity_delta > 0 AND invoice_line_id IS NOT NULL AND purchase_order_line_id IS NOT NULL AND reverses_movement_id IS NULL) OR (movement_type = 'adjustment' AND invoice_line_id IS NULL AND purchase_order_line_id IS NULL AND reverses_movement_id IS NULL) OR (movement_type = 'reversal' AND invoice_line_id IS NULL AND purchase_order_line_id IS NULL AND reverses_movement_id IS NOT NULL)", name="ck_inventory_movements_sources"),
        CheckConstraint("reverses_movement_id IS NULL OR reverses_movement_id <> id", name="ck_inventory_movements_no_self_reversal"),
        Index("uq_inventory_movements_receipt_invoice_line", "invoice_line_id", unique=True, postgresql_where=text("movement_type = 'receipt'")),
        Index("uq_inventory_movements_reversal_target", "reverses_movement_id", unique=True, postgresql_where=text("movement_type = 'reversal'")),
        Index("ix_inventory_movements_product_location_time", "product_id", "location_id", "created_at"),
        Index("ix_inventory_movements_po_line", "purchase_order_line_id"),
    )
