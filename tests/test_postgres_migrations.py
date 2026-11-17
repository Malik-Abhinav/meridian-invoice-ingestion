"""Live PostgreSQL migration and constraint checks in disposable databases."""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, Connection, ForeignKeyConstraint, Index, UniqueConstraint, create_engine, inspect, insert, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from meridian.db import Base
from meridian.models import (
    AuditEvent, InventoryBalance, InventoryMovement, Invoice, InvoiceDocument,
    InvoiceLine, InvoiceUploadAttempt, Location, Product, PurchaseOrder,
    PurchaseOrderLine, Supplier,
)
from meridian.reconciliation_repository import SqlAlchemyTrustedRepository


pytestmark = pytest.mark.postgres
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.fixture
def disposable_database() -> Iterator[Callable[[], str]]:
    source_url = os.environ.get("DATABASE_URL")
    if not source_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    parsed_url = make_url(source_url)
    if parsed_url.get_backend_name() != "postgresql":
        pytest.skip("PostgreSQL integration tests require a PostgreSQL URL")

    admin = create_engine(parsed_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    created: list[str] = []

    def create() -> str:
        name = f"meridian_test_{uuid4().hex[:16]}"
        with admin.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {name}"))
        created.append(name)
        return parsed_url.set(database=name).render_as_string(hide_password=False)

    try:
        yield create
    finally:
        with admin.connect() as connection:
            for name in created:
                connection.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))
        admin.dispose()


def migrate(url: str, target: str) -> None:
    with patch.dict(os.environ, {"DATABASE_URL": url}):
        config = Config(str(ALEMBIC_INI))
        if target == "base":
            command.downgrade(config, "base")
        else:
            command.upgrade(config, target)


@contextmanager
def database_connection(url: str) -> Iterator[Connection]:
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            yield connection
    finally:
        engine.dispose()


def expect_constraint(connection: Connection, statement: object, name: str) -> None:
    with pytest.raises(IntegrityError) as error, connection.begin_nested():
        connection.execute(statement)
    assert name in str(error.value.orig)


def test_reconciliation_repository_reads_existing_tables(disposable_database: Callable[[], str]) -> None:
    url = disposable_database()
    migrate(url, "head")
    engine = create_engine(url)
    try:
        supplier_id, product_id, po_id, po_line_id = (uuid4() for _ in range(4))
        invoice_id, document_id, pending_document_id, changed_document_id = (uuid4() for _ in range(4))
        with engine.begin() as connection:
            connection.execute(insert(Supplier).values(id=supplier_id, code="FICT_R", name="Reference supplier"))
            connection.execute(insert(Product).values(id=product_id, name="Reference product", upc_normalized="012345678905"))
            connection.execute(insert(PurchaseOrder).values(id=po_id, supplier_id=supplier_id,
                                                            order_number="PO-R", status="open"))
            connection.execute(insert(PurchaseOrderLine).values(id=po_line_id, purchase_order_id=po_id,
                                                                line_number=1, product_id=product_id,
                                                                ordered_quantity=5,
                                                                expected_unit_cost=Decimal("1.2500")))
            connection.execute(insert(Invoice).values(id=invoice_id, supplier_id=supplier_id,
                                                      invoice_number_raw=" inv-1 ",
                                                      invoice_number_normalized="INV-1", status="uploaded"))
            connection.execute(insert(InvoiceDocument).values(id=document_id, sha256="a" * 64,
                                                               storage_uri="memory://test", content_type="application/pdf"))
            connection.execute(insert(InvoiceUploadAttempt).values(document_id=document_id,
                                                                   invoice_id=invoice_id,
                                                                   original_filename="test.pdf",
                                                                   identity_status="linked"))
            connection.execute(insert(InvoiceDocument).values(id=changed_document_id, sha256="c" * 64,
                                                               storage_uri="memory://changed", content_type="application/pdf"))
            connection.execute(insert(InvoiceUploadAttempt).values(document_id=changed_document_id,
                                                                   invoice_id=invoice_id,
                                                                   original_filename="changed.pdf",
                                                                   identity_status="conflict"))
            connection.execute(insert(InvoiceDocument).values(id=pending_document_id, sha256="b" * 64,
                                                               storage_uri="memory://pending", content_type="application/pdf"))
            connection.execute(insert(InvoiceUploadAttempt).values(document_id=pending_document_id,
                                                                   original_filename="pending.pdf",
                                                                   identity_status="pending"))
        with Session(engine) as session:
            trusted = SqlAlchemyTrustedRepository(session, supplier_aliases={"FICT_R": ("Alias R",)})
            assert trusted.suppliers()[0].aliases == ("Alias R",)
            assert trusted.products()[0].upc == "012345678905"
            assert trusted.purchase_orders()[0].lines[0].received_quantity == 0
            assert any(item.source_sha256 == "a" * 64 and item.invoice_number_normalized == "INV-1"
                       for item in trusted.prior_invoices())
            assert any(item.source_sha256 == "b" * 64 and item.supplier_id is None
                       for item in trusted.prior_invoices())
            assert {item.source_sha256 for item in trusted.prior_invoices() if item.id == str(invoice_id)} == {
                "a" * 64, "c" * 64}
            assert trusted.prior_invoices() == trusted.prior_invoices()
            assert not session.dirty and not session.new
    finally:
        engine.dispose()


def test_reconciliation_repository_counts_only_posted_receipts_and_reversals(
        disposable_database: Callable[[], str]) -> None:
    url = disposable_database()
    migrate(url, "head")
    engine = create_engine(url)
    supplier_id, product_id, location_id, po_id, po_line_id = (uuid4() for _ in range(5))
    posted_id, failed_id, posted_line_id, failed_line_id = (uuid4() for _ in range(4))
    receipt_id = uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(insert(Supplier).values(id=supplier_id, code="FICT_REC", name="Receipt supplier"))
            connection.execute(insert(Product).values(id=product_id, name="Receipt product", ndc_normalized="00000123456"))
            connection.execute(insert(Location).values(id=location_id, code="REC", name="Receipt location"))
            connection.execute(insert(PurchaseOrder).values(id=po_id, supplier_id=supplier_id,
                                                            order_number="PO-REC", status="open"))
            connection.execute(insert(PurchaseOrderLine).values(id=po_line_id, purchase_order_id=po_id,
                                                                line_number=1, product_id=product_id,
                                                                ordered_quantity=5,
                                                                expected_unit_cost=Decimal("1.2500")))
            for invoice_id, status, invoice_line_id, number in (
                    (posted_id, "posted", posted_line_id, "REC-POSTED"),
                    (failed_id, "failed", failed_line_id, "REC-FAILED")):
                connection.execute(insert(Invoice).values(id=invoice_id, supplier_id=supplier_id,
                                                          invoice_number_normalized=number, status=status))
                connection.execute(insert(InvoiceLine).values(id=invoice_line_id, invoice_id=invoice_id,
                                                              line_number=1, description="Receipt product",
                                                              product_id=product_id, quantity=3,
                                                              unit_cost=Decimal("1.2500"),
                                                              line_total=Decimal("3.7500")))
            connection.execute(insert(InventoryMovement).values(id=receipt_id, product_id=product_id,
                location_id=location_id, movement_type="receipt", quantity_delta=3,
                posting_key="receipt:posted", invoice_line_id=posted_line_id,
                purchase_order_line_id=po_line_id))
        with Session(engine) as session:
            line = SqlAlchemyTrustedRepository(session).purchase_orders()[0].lines[0]
            assert line.received_quantity == 3 and line.receipt_history_complete
        with engine.begin() as connection:
            connection.execute(insert(InventoryMovement).values(product_id=product_id,
                location_id=location_id, movement_type="adjustment", quantity_delta=-1,
                posting_key="adjustment:unrelated"))
        with Session(engine) as session:
            line = SqlAlchemyTrustedRepository(session).purchase_orders()[0].lines[0]
            assert line.received_quantity == 3 and line.receipt_history_complete
        with engine.begin() as connection:
            connection.execute(insert(InventoryMovement).values(product_id=product_id,
                location_id=location_id, movement_type="reversal", quantity_delta=-3,
                posting_key="reversal:posted", reverses_movement_id=receipt_id))
        with Session(engine) as session:
            line = SqlAlchemyTrustedRepository(session).purchase_orders()[0].lines[0]
            assert line.received_quantity == 0 and line.receipt_history_complete
        with engine.begin() as connection:
            connection.execute(insert(InventoryMovement).values(product_id=product_id,
                location_id=location_id, movement_type="receipt", quantity_delta=2,
                posting_key="receipt:failed", invoice_line_id=failed_line_id,
                purchase_order_line_id=po_line_id))
        with Session(engine) as session:
            line = SqlAlchemyTrustedRepository(session).purchase_orders()[0].lines[0]
            assert line.received_quantity == 0 and not line.receipt_history_complete
    finally:
        engine.dispose()


def supplier(connection: Connection, code: str) -> UUID:
    identifier = uuid4()
    connection.execute(insert(Supplier).values(id=identifier, code=code, name=f"Supplier {code}"))
    return identifier


def product(connection: Connection, *, upc: str | None = None, ndc: str | None = None) -> UUID:
    identifier = uuid4()
    connection.execute(insert(Product).values(id=identifier, name="Synthetic product", upc_normalized=upc, ndc_normalized=ndc))
    return identifier


def test_initial_migration_upgrade_downgrade_reupgrade(disposable_database: Callable[[], str]) -> None:
    url = disposable_database()
    engine = create_engine(url)
    try:
        assert inspect(engine).get_table_names() == []
        migrate(url, "head")
        inspector = inspect(engine)
        assert set(Base.metadata.tables) == set(inspector.get_table_names()) - {"alembic_version"}
        for table_name, table in Base.metadata.tables.items():
            expected_checks = {constraint.name for constraint in table.constraints if isinstance(constraint, CheckConstraint)}
            actual_checks = {constraint["name"] for constraint in inspector.get_check_constraints(table_name)}
            assert expected_checks == actual_checks, table_name

            expected_indexes = {index.name for index in table.indexes if isinstance(index, Index)}
            actual_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
            assert expected_indexes <= actual_indexes, table_name

            expected_fks = {
                (tuple(constraint.column_keys), constraint.referred_table.name)
                for constraint in table.constraints if isinstance(constraint, ForeignKeyConstraint)
            }
            actual_fks = {
                (tuple(constraint["constrained_columns"]), constraint["referred_table"])
                for constraint in inspector.get_foreign_keys(table_name)
            }
            assert expected_fks == actual_fks, table_name

            expected_uniques = {
                tuple(column.name for column in constraint.columns)
                for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
            }
            actual_uniques = {
                tuple(constraint["column_names"]) for constraint in inspector.get_unique_constraints(table_name)
            }
            assert expected_uniques <= actual_uniques, table_name
        with patch.dict(os.environ, {"DATABASE_URL": url}):
            command.check(Config(str(ALEMBIC_INI)))
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001_initial"
        migrate(url, "base")
        assert not (set(Base.metadata.tables) & set(inspect(engine).get_table_names()))
        migrate(url, "head")
        assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())

        columns = {column["name"]: column for column in inspect(engine).get_columns("invoices")}
        assert columns["total"]["type"].precision == 18
        assert columns["total"]["type"].scale == 4
        assert columns["created_at"]["type"].timezone
        with engine.connect() as connection:
            version = connection.execute(text("SHOW server_version_num")).scalar_one()
            assert int(version) // 10000 == 16
    finally:
        engine.dispose()


def test_invoice_identity_and_product_constraints(disposable_database: Callable[[], str]) -> None:
    url = disposable_database()
    migrate(url, "head")
    with database_connection(url) as connection:
        first_supplier = supplier(connection, "S1")
        second_supplier = supplier(connection, "S2")
        expect_constraint(connection, insert(Supplier).values(id=uuid4(), code="S1", name="Duplicate"), "suppliers_code_key")
        expect_constraint(connection, insert(Invoice).values(id=uuid4(), supplier_id=uuid4(), status="uploaded"), "invoices_supplier_id_fkey")

        invoice_id = uuid4()
        connection.execute(insert(Invoice).values(id=invoice_id, supplier_id=first_supplier, invoice_number_normalized="INV-1", status="extracted"))
        expect_constraint(connection, insert(Invoice).values(id=uuid4(), supplier_id=first_supplier, invoice_number_normalized="INV-1", status="extracted"), "uq_invoices_supplier_number")
        connection.execute(insert(Invoice).values(id=uuid4(), supplier_id=second_supplier, invoice_number_normalized="INV-1", status="extracted"))
        connection.execute(insert(Invoice).values(id=uuid4(), supplier_id=first_supplier, status="review_required"))
        connection.execute(insert(Invoice).values(id=uuid4(), supplier_id=first_supplier, status="review_required"))
        expect_constraint(connection, insert(Invoice).values(id=uuid4(), supplier_id=first_supplier, status="invalid"), "ck_invoices_status")

        product(connection, upc="012345678905")
        product(connection, ndc="12345678901")
        expect_constraint(connection, insert(Product).values(id=uuid4(), name="No code"), "ck_products_has_identifier")
        expect_constraint(connection, insert(Product).values(id=uuid4(), name="Duplicate UPC", upc_normalized="012345678905"), "uq_products_upc_normalized")
        expect_constraint(connection, insert(Product).values(id=uuid4(), name="Blank NDC", ndc_normalized=" "), "ck_products_ndc_not_blank")

        document_id = uuid4()
        connection.execute(insert(InvoiceDocument).values(id=document_id, sha256="a" * 64, storage_uri="synthetic://invoice.pdf", content_type="application/pdf"))
        expect_constraint(connection, insert(InvoiceDocument).values(id=uuid4(), sha256="a" * 64, storage_uri="synthetic://again.pdf", content_type="application/pdf"), "invoice_documents_sha256_key")
        expect_constraint(connection, insert(InvoiceDocument).values(id=uuid4(), sha256="bad", storage_uri="synthetic://bad.pdf", content_type="application/pdf"), "ck_invoice_documents_sha256_hex")
        connection.execute(insert(InvoiceUploadAttempt).values(id=uuid4(), document_id=document_id, original_filename="first.pdf", identity_status="linked", invoice_id=invoice_id))
        connection.execute(insert(InvoiceUploadAttempt).values(id=uuid4(), document_id=document_id, original_filename="again.pdf", identity_status="duplicate", invoice_id=invoice_id))
        expect_constraint(connection, insert(InvoiceUploadAttempt).values(id=uuid4(), document_id=document_id, original_filename="unlinked.pdf", identity_status="linked"), "ck_upload_attempts_linked_invoice")
        second_document = uuid4()
        connection.execute(insert(InvoiceDocument).values(id=second_document, sha256="b" * 64, storage_uri="synthetic://conflict.pdf", content_type="application/pdf"))
        connection.execute(insert(InvoiceUploadAttempt).values(id=uuid4(), document_id=second_document, original_filename="conflict.pdf", identity_status="conflict", invoice_id=invoice_id))
        assert len(connection.execute(select(InvoiceUploadAttempt.id).where(InvoiceUploadAttempt.invoice_id == invoice_id)).all()) == 3


def test_purchasing_inventory_and_audit_constraints(disposable_database: Callable[[], str]) -> None:
    url = disposable_database()
    migrate(url, "head")
    with database_connection(url) as connection:
        supplier_id = supplier(connection, "S1")
        product_id = product(connection, upc="012345678905")
        location_id = uuid4()
        connection.execute(insert(Location).values(id=location_id, code="MAIN", name="Main"))
        invoice_id = uuid4()
        invoice_line_id = uuid4()
        connection.execute(insert(Invoice).values(id=invoice_id, supplier_id=supplier_id, status="approved"))
        connection.execute(insert(InvoiceLine).values(id=invoice_line_id, invoice_id=invoice_id, line_number=1, description="Synthetic product", product_id=product_id, quantity=2, unit_cost=Decimal("1.2500")))
        purchase_order_id = uuid4()
        po_line_id = uuid4()
        connection.execute(insert(PurchaseOrder).values(id=purchase_order_id, supplier_id=supplier_id, order_number="PO-1", status="open"))
        connection.execute(insert(PurchaseOrderLine).values(id=po_line_id, purchase_order_id=purchase_order_id, line_number=1, product_id=product_id, ordered_quantity=5, expected_unit_cost=Decimal("1.2500")))
        expect_constraint(connection, insert(PurchaseOrderLine).values(id=uuid4(), purchase_order_id=purchase_order_id, line_number=1, product_id=product_id, ordered_quantity=3, expected_unit_cost=Decimal("1.2500")), "uq_purchase_order_lines_order_number")
        expect_constraint(connection, insert(PurchaseOrderLine).values(id=uuid4(), purchase_order_id=purchase_order_id, line_number=2, product_id=product_id, ordered_quantity=0, expected_unit_cost=Decimal("1.2500")), "ck_purchase_order_lines_quantity_positive")
        expect_constraint(connection, insert(PurchaseOrderLine).values(id=uuid4(), purchase_order_id=purchase_order_id, line_number=2, product_id=product_id, ordered_quantity=1, expected_unit_cost=Decimal("-0.01")), "ck_purchase_order_lines_cost_nonnegative")
        expect_constraint(connection, insert(InvoiceLine).values(id=uuid4(), invoice_id=invoice_id, line_number=2, description="Bad quantity", quantity=-1), "ck_invoice_lines_quantity_positive")
        expect_constraint(connection, insert(Invoice).values(id=uuid4(), supplier_id=supplier_id, status="approved", total=Decimal("-0.01")), "ck_invoices_total_nonnegative")

        connection.execute(insert(InventoryBalance).values(id=uuid4(), product_id=product_id, location_id=location_id, available_quantity=0))
        expect_constraint(connection, insert(InventoryBalance).values(id=uuid4(), product_id=product_id, location_id=location_id, available_quantity=1), "uq_inventory_balances_product_location")
        expect_constraint(connection, insert(InventoryBalance).values(id=uuid4(), product_id=product_id, location_id=location_id, available_quantity=-1), "ck_inventory_balances_nonnegative")
        receipt_id = uuid4()
        connection.execute(insert(InventoryMovement).values(id=receipt_id, product_id=product_id, location_id=location_id, movement_type="receipt", quantity_delta=2, posting_key="receipt:one", invoice_line_id=invoice_line_id, purchase_order_line_id=po_line_id))
        expect_constraint(connection, insert(InventoryMovement).values(id=uuid4(), product_id=product_id, location_id=location_id, movement_type="receipt", quantity_delta=2, posting_key="receipt:two", invoice_line_id=invoice_line_id, purchase_order_line_id=po_line_id), "uq_inventory_movements_receipt_invoice_line")
        expect_constraint(connection, insert(InventoryMovement).values(id=uuid4(), product_id=product_id, location_id=location_id, movement_type="adjustment", quantity_delta=1, posting_key="receipt:one"), "inventory_movements_posting_key_key")
        expect_constraint(connection, insert(InventoryMovement).values(id=uuid4(), product_id=product_id, location_id=location_id, movement_type="adjustment", quantity_delta=0, posting_key="zero"), "ck_inventory_movements_nonzero")
        expect_constraint(connection, insert(InventoryMovement).values(id=uuid4(), product_id=product_id, location_id=location_id, movement_type="receipt", quantity_delta=-1, posting_key="negative-receipt", invoice_line_id=uuid4(), purchase_order_line_id=po_line_id), "ck_inventory_movements_sources")
        expect_constraint(connection, insert(InventoryMovement).values(id=uuid4(), product_id=product_id, location_id=location_id, movement_type="adjustment", quantity_delta=1, posting_key="negative-cost", unit_cost=Decimal("-1")), "ck_inventory_movements_cost_nonnegative")
        connection.execute(insert(InventoryMovement).values(id=uuid4(), product_id=product_id, location_id=location_id, movement_type="reversal", quantity_delta=-2, posting_key="reversal:one", reverses_movement_id=receipt_id))
        expect_constraint(connection, insert(InventoryMovement).values(id=uuid4(), product_id=product_id, location_id=location_id, movement_type="reversal", quantity_delta=-2, posting_key="reversal:two", reverses_movement_id=receipt_id), "uq_inventory_movements_reversal_target")

        connection.execute(insert(AuditEvent).values(id=uuid4(), actor_type="system", action="seed", entity_type="invoice", entity_id=invoice_id, after_data={"status": "approved"}))
        expect_constraint(connection, insert(AuditEvent).values(id=uuid4(), actor_type="staff", action="approve", entity_type="invoice", entity_id=invoice_id), "ck_audit_events_staff_actor")


def test_timestamp_round_trip_and_postgresql_types(disposable_database: Callable[[], str]) -> None:
    url = disposable_database()
    migrate(url, "head")
    with database_connection(url) as connection:
        connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        supplier_id = supplier(connection, "S1")
        original = datetime(2026, 11, 13, 18, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        invoice_id = uuid4()
        connection.execute(insert(Invoice).values(id=invoice_id, supplier_id=supplier_id, status="uploaded", created_at=original, total=Decimal("1.2345")))
        stored = connection.execute(select(Invoice.created_at, Invoice.total).where(Invoice.id == invoice_id)).one()
        assert stored.created_at == datetime(2026, 11, 13, 13, 0, tzinfo=timezone.utc)
        assert stored.total == Decimal("1.2345")
        type_rows = connection.execute(text("SELECT column_name, data_type, numeric_precision, numeric_scale FROM information_schema.columns WHERE table_name = 'invoices' AND column_name IN ('created_at', 'total')")).all()
        types = {row.column_name: row for row in type_rows}
        assert types["created_at"].data_type == "timestamp with time zone"
        assert (types["total"].numeric_precision, types["total"].numeric_scale) == (18, 4)
