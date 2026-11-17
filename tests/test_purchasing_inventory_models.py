from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import CheckConstraint, DateTime, Numeric, create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, configure_mappers
from sqlalchemy.schema import CreateIndex, CreateTable

from meridian.db import Base
from meridian.models import (
    AuditEvent, InventoryBalance, InventoryMovement, Location, Product,
    PurchaseOrder, PurchaseOrderLine, Supplier,
)


@pytest.fixture
def sample_db() -> tuple[object, UUID, UUID, UUID, UUID]:
    """SQLite checks generic SQL constraints; PostgreSQL remains unverified."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[table for table in Base.metadata.sorted_tables if table.name not in {
            "audit_events", "invoice_documents", "invoice_upload_attempts",
            "invoice_posting_authorizations", "invoice_processing_attempts",
            "invoice_review_states"}],
    )
    with Session(engine) as session:
        supplier = Supplier(code="WHOLESALE", name="Wholesaler")
        product = Product(name="Sample product", upc_normalized="012345678905")
        location = Location(code="MAIN", name="Main shelf")
        order = PurchaseOrder(supplier=supplier, order_number="PO-1", status="open")
        session.add_all([supplier, product, location, order])
        session.commit()
        ids = (supplier.id, product.id, location.id, order.id)
    yield engine, *ids
    engine.dispose()


def _checks(model: type[Base]) -> set[str]:
    return {str(item.sqltext) for item in model.__table__.constraints if isinstance(item, CheckConstraint)}


def test_tables_relationships_and_foreign_keys() -> None:
    configure_mappers()
    assert {"purchase_orders", "purchase_order_lines", "locations", "inventory_balances", "inventory_movements", "audit_events"} <= set(Base.metadata.tables)
    assert PurchaseOrder.supplier.property.mapper.class_ is Supplier
    assert PurchaseOrderLine.purchase_order.property.mapper.class_ is PurchaseOrder
    assert InventoryMovement.purchase_order_line.property.mapper.class_ is PurchaseOrderLine
    assert InventoryMovement.invoice_line.property.mapper.class_.__name__ == "InvoiceLine"
    assert InventoryBalance.location.property.mapper.class_ is Location
    for model, column, target in (
        (PurchaseOrder, "supplier_id", "suppliers.id"),
        (PurchaseOrderLine, "purchase_order_id", "purchase_orders.id"),
        (InventoryBalance, "product_id", "products.id"),
        (InventoryBalance, "location_id", "locations.id"),
        (InventoryMovement, "invoice_line_id", "invoice_lines.id"),
        (InventoryMovement, "purchase_order_line_id", "purchase_order_lines.id"),
        (InventoryMovement, "reverses_movement_id", "inventory_movements.id"),
    ):
        assert {str(fk.column) for fk in model.__table__.c[column].foreign_keys} == {target}


def test_purchase_order_line_number_is_unique(sample_db: tuple[object, UUID, UUID, UUID, UUID]) -> None:
    engine, _, product_id, _, order_id = sample_db
    with Session(engine) as session:
        session.add(PurchaseOrderLine(purchase_order_id=order_id, product_id=product_id, line_number=1, ordered_quantity=10, expected_unit_cost=Decimal("1.2500")))
        session.commit()
        session.add(PurchaseOrderLine(purchase_order_id=order_id, product_id=product_id, line_number=1, ordered_quantity=5, expected_unit_cost=Decimal("1.2500")))
        with pytest.raises(IntegrityError):
            session.flush()


@pytest.mark.parametrize("quantity", [0, -1])
def test_invalid_ordered_quantity_is_rejected_by_generic_check(sample_db: tuple[object, UUID, UUID, UUID, UUID], quantity: int) -> None:
    engine, _, product_id, _, order_id = sample_db
    with Session(engine) as session:
        session.add(PurchaseOrderLine(purchase_order_id=order_id, product_id=product_id, line_number=1, ordered_quantity=quantity, expected_unit_cost=Decimal("1.0000")))
        with pytest.raises(IntegrityError):
            session.flush()


def test_valid_ordered_quantity_and_cost(sample_db: tuple[object, UUID, UUID, UUID, UUID]) -> None:
    engine, _, product_id, _, order_id = sample_db
    with Session(engine) as session:
        session.add(PurchaseOrderLine(purchase_order_id=order_id, product_id=product_id, line_number=1, ordered_quantity=1, expected_unit_cost=Decimal("0.1234")))
        session.commit()


def test_balance_uniqueness_and_nonnegative_quantity(sample_db: tuple[object, UUID, UUID, UUID, UUID]) -> None:
    engine, _, product_id, location_id, _ = sample_db
    with Session(engine) as session:
        session.add(InventoryBalance(product_id=product_id, location_id=location_id, available_quantity=-1))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
        session.add(InventoryBalance(product_id=product_id, location_id=location_id, available_quantity=0))
        session.commit()
        session.add(InventoryBalance(product_id=product_id, location_id=location_id, available_quantity=1))
        with pytest.raises(IntegrityError):
            session.flush()


def test_movement_posting_key_is_unique(sample_db: tuple[object, UUID, UUID, UUID, UUID]) -> None:
    engine, _, product_id, location_id, _ = sample_db
    with Session(engine) as session:
        session.add(InventoryMovement(product_id=product_id, location_id=location_id, movement_type="adjustment", quantity_delta=1, posting_key="adjustment:one"))
        session.commit()
        session.add(InventoryMovement(product_id=product_id, location_id=location_id, movement_type="adjustment", quantity_delta=1, posting_key="adjustment:one"))
        with pytest.raises(IntegrityError):
            session.flush()


def test_zero_movement_is_rejected_by_generic_check(sample_db: tuple[object, UUID, UUID, UUID, UUID]) -> None:
    engine, _, product_id, location_id, _ = sample_db
    with Session(engine) as session:
        session.add(InventoryMovement(product_id=product_id, location_id=location_id, movement_type="adjustment", quantity_delta=0, posting_key="adjustment:zero"))
        with pytest.raises(IntegrityError):
            session.flush()


def test_movement_type_and_quantity_checks_are_declared() -> None:
    checks = _checks(InventoryMovement)
    assert "quantity_delta <> 0" in checks
    assert any("movement_type = 'receipt'" in item and "invoice_line_id IS NOT NULL" in item for item in checks)
    assert any("movement_type = 'reversal'" in item and "reverses_movement_id IS NOT NULL" in item for item in checks)
    assert "available_quantity >= 0" in _checks(InventoryBalance)
    assert "ordered_quantity > 0" in _checks(PurchaseOrderLine)
    assert "status IN ('draft', 'open', 'closed', 'cancelled')" in _checks(PurchaseOrder)
    assert "received_quantity" not in PurchaseOrderLine.__table__.c


def test_postgresql_idempotency_indexes_are_declared() -> None:
    assert InventoryMovement.__table__.c.posting_key.unique
    for name, predicate in (
        ("uq_inventory_movements_receipt_invoice_line", "movement_type = 'receipt'"),
        ("uq_inventory_movements_reversal_target", "movement_type = 'reversal'"),
    ):
        index = next(index for index in InventoryMovement.__table__.indexes if index.name == name)
        ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        assert index.unique and f"WHERE {predicate}" in ddl
    balance_index = next(index for index in InventoryBalance.__table__.indexes if index.name == "uq_inventory_balances_product_location")
    assert balance_index.unique


def test_money_timestamp_and_audit_schema() -> None:
    for model, column in ((PurchaseOrderLine, "expected_unit_cost"), (InventoryMovement, "unit_cost")):
        sql_type = model.__table__.c[column].type
        assert isinstance(sql_type, Numeric)
        assert (sql_type.precision, sql_type.scale, sql_type.asdecimal) == (18, 4, True)
    for model, column in ((PurchaseOrder, "created_at"), (InventoryBalance, "updated_at"), (InventoryMovement, "created_at"), (AuditEvent, "created_at")):
        assert isinstance(model.__table__.c[column].type, DateTime)
        assert model.__table__.c[column].type.timezone
    ddl = str(CreateTable(AuditEvent.__table__).compile(dialect=postgresql.dialect()))
    assert "JSONB" in ddl
    assert any("actor_type <> 'staff'" in check for check in _checks(AuditEvent))
    assert AuditEvent.__table__.c.before_data.nullable
    assert AuditEvent.__table__.c.after_data.nullable
    assert not AuditEvent.__table__.c.entity_id.foreign_keys
