import re

import pytest
from sqlalchemy import CheckConstraint, DateTime, Numeric, Uuid
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateIndex, CreateTable

from meridian.db import Base, database_url
from meridian.domain import InvoiceState
from meridian.models import Invoice, InvoiceDocument, InvoiceLine, InvoiceUploadAttempt, Product, Supplier


def test_database_url_requires_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        database_url()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:secret@localhost/meridian")
    assert database_url().endswith("/meridian")


def test_expected_tables_and_relationships() -> None:
    configure_mappers()
    assert set(Base.metadata.tables) == {
        "suppliers", "products", "invoices", "invoice_lines", "invoice_documents", "invoice_upload_attempts"
    }
    assert Invoice.supplier.property.mapper.class_ is Supplier
    assert Invoice.lines.property.mapper.class_ is InvoiceLine
    assert Invoice.upload_attempts.property.mapper.class_ is InvoiceUploadAttempt
    assert InvoiceUploadAttempt.document.property.mapper.class_ is InvoiceDocument
    assert InvoiceLine.product.property.mapper.class_ is Product


def test_upload_attempts_are_separate_from_canonical_invoice() -> None:
    document = InvoiceDocument(sha256="a" * 64, storage_uri="file:///example.pdf", content_type="application/pdf")
    first = InvoiceUploadAttempt(document=document, original_filename="first.pdf", identity_status="pending")
    second = InvoiceUploadAttempt(document=document, original_filename="again.pdf", identity_status="pending")
    assert first is not second
    assert first.document is second.document
    assert first.invoice is None and second.invoice is None


def test_invoice_number_uniqueness_is_supplier_scoped_and_only_for_present_numbers() -> None:
    index = next(index for index in Invoice.__table__.indexes if index.name == "uq_invoices_supplier_number")
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert index.unique
    assert "(supplier_id, invoice_number_normalized)" in ddl
    assert "WHERE invoice_number_normalized IS NOT NULL" in ddl
    assert Invoice.__table__.c.invoice_number_normalized.nullable


def test_document_hash_unique_and_upload_attempts_can_repeat() -> None:
    assert InvoiceDocument.__table__.c.sha256.unique
    assert not InvoiceUploadAttempt.__table__.c.document_id.unique
    assert InvoiceUploadAttempt.__table__.c.invoice_id.nullable
    assert "sha256 ~ '^[0-9a-f]{64}$'" in _checks(InvoiceDocument)


def test_product_identifiers_are_individually_optional_and_unique_when_present() -> None:
    assert Product.__table__.c.upc_normalized.nullable
    assert Product.__table__.c.ndc_normalized.nullable
    assert "upc_normalized IS NOT NULL OR ndc_normalized IS NOT NULL" in _checks(Product)
    for name, column in (("uq_products_upc_normalized", "upc_normalized"), ("uq_products_ndc_normalized", "ndc_normalized")):
        index = next(index for index in Product.__table__.indexes if index.name == name)
        ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        assert index.unique
        assert f"WHERE {column} IS NOT NULL" in ddl


def test_foreign_keys_checks_and_money_types() -> None:
    for model, column, target in (
        (Invoice, "supplier_id", "suppliers.id"),
        (InvoiceLine, "invoice_id", "invoices.id"),
        (InvoiceLine, "product_id", "products.id"),
        (InvoiceUploadAttempt, "document_id", "invoice_documents.id"),
        (InvoiceUploadAttempt, "invoice_id", "invoices.id"),
    ):
        assert {str(fk.column) for fk in model.__table__.c[column].foreign_keys} == {target}
    assert "quantity IS NULL OR quantity > 0" in _checks(InvoiceLine)
    assert "identity_status <> 'linked' OR invoice_id IS NOT NULL" in _checks(InvoiceUploadAttempt)
    for model, columns in ((Invoice, ("subtotal", "tax", "total")), (InvoiceLine, ("unit_cost", "line_total"))):
        for column in columns:
            column_type = model.__table__.c[column].type
            assert isinstance(column_type, Numeric)
            assert (column_type.precision, column_type.scale, column_type.asdecimal) == (18, 4, True)


def test_uuid_and_timezone_columns_compile_for_postgresql() -> None:
    for model in (Supplier, Product, Invoice, InvoiceLine, InvoiceDocument, InvoiceUploadAttempt):
        assert isinstance(model.__table__.c.id.type, Uuid)
        ddl = str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        assert "UUID" in ddl
        for column in model.__table__.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone
                assert "TIMESTAMP WITH TIME ZONE" in ddl


def test_schema_status_values_match_domain_enum() -> None:
    status_check = next(check for check in Invoice.__table__.constraints if isinstance(check, CheckConstraint) and check.name == "ck_invoices_status")
    assert {f"'{state.value}'" for state in InvoiceState} == set(re.findall(r"'[^']+'", str(status_check.sqltext)))


def test_invoice_models_leave_unknown_fields_nullable() -> None:
    assert Invoice.__table__.c.invoice_date.nullable
    assert Invoice.__table__.c.total.nullable
    assert InvoiceLine.__table__.c.product_id.nullable
    assert InvoiceLine.__table__.c.quantity.nullable
    assert InvoiceLine.__table__.c.unit_cost.nullable


def _checks(model: type[Base]) -> set[str]:
    return {str(check.sqltext) for check in model.__table__.constraints if isinstance(check, CheckConstraint)}
