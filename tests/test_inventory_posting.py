"""Disposable PostgreSQL tests of atomic receipt posting and approval evidence."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from meridian.evaluation_fixtures import FixtureGroundTruth
from meridian.inventory_posting import (
    AuthorizationRequest, LineAllocation, PostingConflict, PostingErrorCode,
    PostingRequest, PostingStatus, post_invoice, prepare_posting_approval,
    record_posting_authorization,
)
from meridian.models import (
    AuditEvent, InventoryBalance, InventoryMovement, Invoice, InvoiceDocument,
    InvoiceLine, InvoicePostingAuthorization, InvoiceUploadAttempt, Location,
    Product, PurchaseOrder, PurchaseOrderLine, Supplier,
)
from meridian.reconciliation_evaluation import controlled_extraction


ROOT = Path(__file__).resolve().parents[1]
TRUTH = FixtureGroundTruth.model_validate_json(
    (ROOT / "output/evaluation/ground_truth/fixture_a_clean_01.json").read_text())


@pytest.fixture
def database():
    source = os.environ.get("DATABASE_URL")
    if not source or make_url(source).get_backend_name() != "postgresql":
        pytest.skip("Disposable PostgreSQL database is required")
    parsed = make_url(source)
    name = f"meridian_posting_{uuid4().hex[:16]}"
    admin = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"CREATE DATABASE {name}"))
    url = parsed.set(database=name).render_as_string(hide_password=False)
    try:
        with patch.dict(os.environ, {"DATABASE_URL": url}):
            command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        engine = create_engine(url, isolation_level="READ COMMITTED")
        try:
            yield engine, sessionmaker(engine, expire_on_commit=False)
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))
        admin.dispose()


@dataclass(frozen=True)
class Seeded:
    invoice_id: UUID
    location_id: UUID
    authorization_id: UUID
    allocations: tuple[LineAllocation, ...]
    product_ids: tuple[UUID, ...]
    po_line_ids: tuple[UUID, ...]

    def request(self, key: str = "posting-one", *, location_id: UUID | None = None,
                allocations: tuple[LineAllocation, ...] | None = None) -> PostingRequest:
        return PostingRequest(invoice_id=self.invoice_id, authorization_id=self.authorization_id,
                              location_id=location_id or self.location_id,
                              idempotency_key=key, allocations=allocations or self.allocations)


def seed(factory: sessionmaker[Session], *, source_hash: str = "f" * 64,
         number: str | None = None, ordered_quantities: tuple[int, ...] | None = None,
         invoice_quantities: tuple[int, ...] | None = None,
         reuse: Seeded | None = None, authorize: bool = True,
         po_status: str = "open", new_po_reference: str | None = None,
         existing_po_reference: str | None = None,
         repeat_first_line: bool = False) -> Seeded:
    visible = TRUTH.displayed_invoice
    if number is not None:
        visible = visible.model_copy(update={"invoice_number": number})
    if new_po_reference is not None:
        visible = visible.model_copy(update={"purchase_order_reference": new_po_reference})
    if existing_po_reference is not None:
        visible = visible.model_copy(update={"purchase_order_reference": existing_po_reference})
    if invoice_quantities is not None:
        changed_lines = tuple(line.model_copy(update={
            "quantity": quantity, "line_total": quantity * line.unit_cost})
            for line, quantity in zip(visible.lines, invoice_quantities, strict=True))
        subtotal = sum((line.line_total for line in changed_lines), Decimal("0"))
        totals = visible.displayed_totals.model_copy(update={
            "subtotal": subtotal, "total": subtotal + visible.displayed_totals.tax})
        visible = visible.model_copy(update={"lines": changed_lines,
                                             "displayed_totals": totals})
    if repeat_first_line:
        repeated = visible.lines[0].model_copy(update={
            "line_number": len(visible.lines) + 1, "quantity": 1,
            "line_total": visible.lines[0].unit_cost})
        changed_lines = visible.lines + (repeated,)
        subtotal = sum((line.line_total for line in changed_lines), Decimal("0"))
        totals = visible.displayed_totals.model_copy(update={
            "subtotal": subtotal, "total": subtotal + visible.displayed_totals.tax})
        visible = visible.model_copy(update={"lines": changed_lines,
                                             "displayed_totals": totals})
    truth = TRUTH.model_copy(update={"displayed_invoice": visible})
    extraction, ocr = controlled_extraction(truth, source_sha256=source_hash)
    with factory.begin() as session:
        if reuse is None:
            supplier = Supplier(code="FICT_A", name=visible.supplier.name)
            location = Location(code="MAIN", name="Main inventory")
            session.add_all((supplier, location))
            session.flush()
            product_by_sku = {}
            for product in visible.products:
                record = Product(name=product.description, upc_normalized=product.upc,
                                 ndc_normalized=product.ndc)
                session.add(record)
                session.flush()
                product_by_sku[product.sku] = record
            po = PurchaseOrder(supplier_id=supplier.id,
                               order_number=visible.purchase_order_reference, status=po_status)
            session.add(po)
            session.flush()
            po_by_sku = {}
            for index, line in enumerate(visible.lines):
                po_line = PurchaseOrderLine(
                    purchase_order_id=po.id, line_number=index + 1,
                    product_id=product_by_sku[line.product_sku].id,
                    ordered_quantity=(ordered_quantities[index] if ordered_quantities else line.quantity),
                    expected_unit_cost=line.unit_cost)
                session.add(po_line)
                session.flush()
                po_by_sku[line.product_sku] = po_line
            location_id = location.id
        else:
            supplier = session.scalar(select(Supplier).where(Supplier.code == "FICT_A"))
            product_by_sku = {product.sku: session.get(Product, product_id)
                              for product, product_id in zip(visible.products, reuse.product_ids, strict=True)}
            if new_po_reference is None:
                po_by_sku = {product.sku: session.get(PurchaseOrderLine, po_id)
                             for product, po_id in zip(visible.products,
                                                       reuse.po_line_ids[:len(visible.products)], strict=True)}
            else:
                po = PurchaseOrder(supplier_id=supplier.id,
                                   order_number=new_po_reference, status=po_status)
                session.add(po)
                session.flush()
                po_by_sku = {}
                for index, line in enumerate(visible.lines):
                    po_line = PurchaseOrderLine(purchase_order_id=po.id,
                        line_number=index + 1, product_id=product_by_sku[line.product_sku].id,
                        ordered_quantity=(ordered_quantities[index] if ordered_quantities else line.quantity),
                        expected_unit_cost=line.unit_cost)
                    session.add(po_line)
                    session.flush()
                    po_by_sku[line.product_sku] = po_line
            location_id = reuse.location_id
        invoice = Invoice(supplier_id=supplier.id, invoice_number_raw=visible.invoice_number,
                          invoice_number_normalized=visible.invoice_number,
                          invoice_date=visible.invoice_date, status="approved",
                          subtotal=visible.displayed_totals.subtotal,
                          tax=visible.displayed_totals.tax, total=visible.displayed_totals.total)
        session.add(invoice)
        session.flush()
        document = InvoiceDocument(sha256=source_hash,
                                   storage_uri=f"memory://{source_hash}", content_type="application/pdf")
        session.add(document)
        session.flush()
        session.add(InvoiceUploadAttempt(document_id=document.id, invoice_id=invoice.id,
                                         original_filename="controlled.pdf", identity_status="linked"))
        allocations = []
        invoice_lines = []
        for row, line in enumerate(visible.lines, 1):
            product = next(item for item in visible.products if item.sku == line.product_sku)
            stored = InvoiceLine(invoice_id=invoice.id, line_number=line.line_number,
                                 description=product.description,
                                 product_id=product_by_sku[line.product_sku].id,
                                 upc_raw=product.upc, ndc_raw=product.ndc,
                                 quantity=line.quantity, unit_cost=line.unit_cost,
                                 line_total=line.line_total)
            session.add(stored)
            session.flush()
            invoice_lines.append(stored)
            allocations.append(LineAllocation(invoice_line_id=stored.id,
                purchase_order_line_id=po_by_sku[line.product_sku].id,
                source_row_order=row))
        session.flush()
        if authorize:
            evidence = prepare_posting_approval(session, invoice.id, location_id, extraction, ocr,
                                                tuple(allocations))
            event = AuditEvent(actor_type="staff", actor_id="reviewer-1", action="approve",
                               entity_type="invoice", entity_id=invoice.id,
                               after_data={"status": "approved",
                                           "posting_evidence": evidence.model_dump(mode="json")})
            session.add(event)
            session.flush()
            authorization = record_posting_authorization(session,
                AuthorizationRequest(invoice_id=invoice.id, approval_event_id=event.id,
                                     location_id=location_id, allocations=tuple(allocations)), extraction, ocr)
            authorization_id = authorization.id
        else:
            authorization_id = uuid4()
        return Seeded(invoice.id, location_id, authorization_id, tuple(allocations),
                      tuple(product_by_sku[item.sku].id for item in visible.products),
                      tuple(po_by_sku[item.product_sku].id for item in visible.lines))


def state(factory: sessionmaker[Session], invoice_id: UUID):
    with factory() as session:
        invoice = session.get(Invoice, invoice_id)
        movements = list(session.scalars(select(InventoryMovement).where(
            InventoryMovement.invoice_line_id.in_(select(InvoiceLine.id).where(
                InvoiceLine.invoice_id == invoice_id)))))
        balances = {item.product_id: item.available_quantity for item in session.scalars(
            select(InventoryBalance))}
        return invoice.status, movements, balances


def authorize_existing(factory: sessionmaker[Session], item: Seeded,
                       allocations: tuple[LineAllocation, ...] | None = None) -> Seeded:
    allocations = allocations or item.allocations
    extraction, ocr = controlled_extraction(TRUTH)
    with factory.begin() as session:
        evidence = prepare_posting_approval(session, item.invoice_id, item.location_id, extraction, ocr,
                                            allocations)
        approval = AuditEvent(actor_type="staff", actor_id="reviewer-1", action="approve",
                              entity_type="invoice", entity_id=item.invoice_id,
                              after_data={"status": "approved",
                                          "posting_evidence": evidence.model_dump(mode="json")})
        session.add(approval)
        session.flush()
        authorization = record_posting_authorization(session, AuthorizationRequest(
            invoice_id=item.invoice_id, approval_event_id=approval.id,
            location_id=item.location_id, allocations=allocations), extraction, ocr)
        return replace(item, authorization_id=authorization.id, allocations=allocations)


def test_success_retry_ledger_balance_and_exact_decimal(database) -> None:
    _, factory = database
    item = seed(factory)
    first = post_invoice(factory, item.request())
    assert first.status == PostingStatus.POSTED
    status, movements, balances = state(factory, item.invoice_id)
    assert status == "posted" and len(movements) == 2
    assert {m.invoice_line_id for m in movements} == {a.invoice_line_id for a in item.allocations}
    assert {m.purchase_order_line_id for m in movements} == set(item.po_line_ids)
    assert {m.unit_cost for m in movements} == {Decimal("4.1250"), Decimal("7.5000")}
    assert balances == dict(zip(item.product_ids, (2, 3), strict=True))
    again = post_invoice(factory, item.request())
    assert again.status == PostingStatus.ALREADY_POSTED
    assert again.movement_ids == first.movement_ids
    assert len(state(factory, item.invoice_id)[1]) == 2
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request("new-key"))
    assert error.value.code == PostingErrorCode.IDEMPOTENCY_CONFLICT


def test_missing_invoice_or_approval_blocks_without_writes(database) -> None:
    _, factory = database
    item = seed(factory, authorize=False)
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request())
    assert error.value.code == PostingErrorCode.MISSING_APPROVAL_EVIDENCE
    nonexistent = item.request().model_copy(update={"invoice_id": uuid4()})
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, nonexistent)
    assert error.value.code == PostingErrorCode.INVOICE_NOT_FOUND
    assert state(factory, item.invoice_id) == ("approved", [], {})


def test_same_key_changed_request_and_missing_location(database) -> None:
    _, factory = database
    item = seed(factory)
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request(location_id=uuid4()))
    assert error.value.code == PostingErrorCode.STALE_RECONCILIATION
    assert state(factory, item.invoice_id) == ("approved", [], {})
    post_invoice(factory, item.request())
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request(location_id=uuid4()))
    assert error.value.code == PostingErrorCode.IDEMPOTENCY_CONFLICT
    assert len(state(factory, item.invoice_id)[1]) == 2


def test_approval_binds_existing_target_location(database) -> None:
    _, factory = database
    item = seed(factory, authorize=False)
    extraction, ocr = controlled_extraction(TRUTH)
    with factory() as session:
        with pytest.raises(PostingConflict) as error:
            prepare_posting_approval(session, item.invoice_id, uuid4(), extraction, ocr,
                                     item.allocations)
    assert error.value.code == PostingErrorCode.INVALID_INVENTORY_LOCATION
    approved = authorize_existing(factory, item)
    with factory.begin() as session:
        alternate = Location(code="ALT", name="Alternate location")
        session.add(alternate)
        session.flush()
        alternate_id = alternate.id
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, approved.request(location_id=alternate_id))
    assert error.value.code == PostingErrorCode.STALE_RECONCILIATION
    assert state(factory, item.invoice_id) == ("approved", [], {})


def test_stale_invoice_po_status_supplier_price_and_allocation(database) -> None:
    _, factory = database
    item = seed(factory)
    with factory.begin() as session:
        po_line = session.get(PurchaseOrderLine, item.po_line_ids[0])
        po = session.get(PurchaseOrder, po_line.purchase_order_id)
        po.status = "closed"
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request())
    assert error.value.code == PostingErrorCode.PO_INELIGIBLE
    with factory.begin() as session:
        session.get(PurchaseOrder, session.get(PurchaseOrderLine, item.po_line_ids[0]).purchase_order_id).status = "open"
        session.get(PurchaseOrderLine, item.po_line_ids[0]).expected_unit_cost = Decimal("5.0000")
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request())
    assert error.value.code == PostingErrorCode.PRICE_MISMATCH
    with factory.begin() as session:
        po_line = session.get(PurchaseOrderLine, item.po_line_ids[0])
        po_line.expected_unit_cost = Decimal("4.1250")
        other_supplier = Supplier(code="OTHER", name="Other supplier")
        session.add(other_supplier)
        session.flush()
        session.get(PurchaseOrder, po_line.purchase_order_id).supplier_id = other_supplier.id
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request())
    assert error.value.code == PostingErrorCode.SUPPLIER_MISMATCH
    with factory.begin() as session:
        po = session.get(PurchaseOrder, session.get(PurchaseOrderLine, item.po_line_ids[0]).purchase_order_id)
        po.supplier_id = session.get(Invoice, item.invoice_id).supplier_id
    wrong = list(item.allocations)
    wrong[0] = wrong[0].model_copy(update={"purchase_order_line_id": item.po_line_ids[1]})
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request(allocations=tuple(wrong)))
    assert error.value.code == PostingErrorCode.STALE_RECONCILIATION
    with factory.begin() as session:
        session.get(InvoiceLine, item.allocations[0].invoice_line_id).quantity = 1
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request())
    assert error.value.code == PostingErrorCode.STALE_RECONCILIATION
    assert state(factory, item.invoice_id) == ("approved", [], {})


def test_exact_partial_full_and_over_receipt(database) -> None:
    _, factory = database
    first = seed(factory, ordered_quantities=(3, 4))
    second = seed(factory, reuse=first, number="FICT_A-INV-SECOND",
                  source_hash="a" * 64, invoice_quantities=(1, 1))
    third = seed(factory, reuse=first, number="FICT_A-INV-THIRD",
                 source_hash="b" * 64)
    post_invoice(factory, first.request())
    post_invoice(factory, second.request("posting-two"))
    assert state(factory, second.invoice_id)[0] == "posted"
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, third.request("posting-three"))
    assert error.value.code == PostingErrorCode.INSUFFICIENT_PO_QUANTITY
    # The second posting used exactly the remaining quantity; balances reflect both.
    assert state(factory, second.invoice_id)[2] == dict(zip(first.product_ids, (3, 4), strict=True))


def test_concurrent_identical_requests_post_once(database) -> None:
    _, factory = database
    item = seed(factory)
    barrier = Barrier(2)
    def worker():
        barrier.wait()
        return post_invoice(factory, item.request())
    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = list(pool.map(lambda _: worker(), range(2)))
    assert {one.status, two.status} == {PostingStatus.POSTED, PostingStatus.ALREADY_POSTED}
    assert one.movement_ids == two.movement_ids
    assert len(state(factory, item.invoice_id)[1]) == 2


def test_concurrent_competing_invoices_serialize_on_po_lines(database) -> None:
    _, factory = database
    first = seed(factory)
    second = seed(factory, reuse=first, number="FICT_A-INV-COMPETE",
                  source_hash="a" * 64)
    barrier = Barrier(2)
    def worker(item, key):
        barrier.wait()
        try:
            return post_invoice(factory, item.request(key)).status
        except PostingConflict as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda pair: worker(*pair),
                                 ((first, "first"), (second, "second"))))
    assert set(outcomes) == {PostingStatus.POSTED, PostingErrorCode.INSUFFICIENT_PO_QUANTITY}
    assert sum(len(state(factory, item.invoice_id)[1]) for item in (first, second)) == 2


def test_concurrent_independent_pos_share_balance_without_lost_update(database) -> None:
    _, factory = database
    first = seed(factory)
    second = seed(factory, reuse=first, number="FICT_A-INV-OTHER-PO",
                  source_hash="a" * 64, new_po_reference="FICT_A-PO-OTHER")
    barrier = Barrier(2)
    def worker(item, key):
        barrier.wait()
        return post_invoice(factory, item.request(key))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: worker(*pair),
                                ((first, "one"), (second, "two"))))
    assert all(result.status == PostingStatus.POSTED for result in results)
    assert state(factory, first.invoice_id)[2] == dict(zip(first.product_ids, (4, 6), strict=True))


def test_concurrent_different_keys_for_same_invoice_conflict(database) -> None:
    _, factory = database
    item = seed(factory)
    barrier = Barrier(2)
    def worker(key):
        barrier.wait()
        try:
            return post_invoice(factory, item.request(key)).status
        except PostingConflict as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(worker, ("one", "two")))
    assert set(outcomes) == {PostingStatus.POSTED, PostingErrorCode.IDEMPOTENCY_CONFLICT}
    assert len(state(factory, item.invoice_id)[1]) == 2


def test_missing_and_ambiguous_allocation_are_rejected_before_authorization(database) -> None:
    _, factory = database
    item = seed(factory, authorize=False)
    extraction, ocr = controlled_extraction(TRUTH)
    with factory() as session:
        with pytest.raises(PostingConflict) as missing:
            prepare_posting_approval(session, item.invoice_id, item.location_id, extraction, ocr,
                                     item.allocations[:1])
        assert missing.value.code == PostingErrorCode.MISSING_PO_ALLOCATION
        duplicate = item.allocations[0].model_copy(update={
            "invoice_line_id": item.allocations[1].invoice_line_id})
        with pytest.raises(PostingConflict) as ambiguous:
            prepare_posting_approval(session, item.invoice_id, item.location_id, extraction, ocr,
                                     (item.allocations[0], duplicate))
        assert ambiguous.value.code == PostingErrorCode.AMBIGUOUS_PO_ALLOCATION


def test_missing_staff_event_and_invalid_state_cannot_authorize_or_post(database) -> None:
    _, factory = database
    item = seed(factory, authorize=False)
    extraction, ocr = controlled_extraction(TRUTH)
    with factory.begin() as session:
        with pytest.raises(PostingConflict) as missing:
            record_posting_authorization(session, AuthorizationRequest(
                invoice_id=item.invoice_id, approval_event_id=uuid4(),
                location_id=item.location_id, allocations=item.allocations), extraction, ocr)
        assert missing.value.code == PostingErrorCode.MISSING_APPROVAL_EVIDENCE
    with factory.begin() as session:
        session.get(Invoice, item.invoice_id).status = "validated"
    with factory() as session:
        with pytest.raises(PostingConflict) as state_error:
            record_posting_authorization(session, AuthorizationRequest(
                invoice_id=item.invoice_id, approval_event_id=uuid4(),
                location_id=item.location_id, allocations=item.allocations), extraction, ocr)
        assert state_error.value.code == PostingErrorCode.INVALID_INVOICE_STATE


def test_duplicate_identity_is_rechecked_after_authorization(database) -> None:
    _, factory = database
    item = seed(factory)
    with factory.begin() as session:
        supplier_id = session.get(Invoice, item.invoice_id).supplier_id
        session.add(Invoice(supplier_id=supplier_id,
            invoice_number_normalized=" fict_a-inv-0001 ", status="uploaded"))
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request())
    assert error.value.code == PostingErrorCode.DUPLICATE_INVOICE_IDENTITY
    assert state(factory, item.invoice_id) == ("approved", [], {})


def test_invalid_historical_reversal_blocks_posting(database) -> None:
    _, factory = database
    first = seed(factory, ordered_quantities=(4, 6))
    second = seed(factory, reuse=first, number="FICT_A-INV-RECEIPT",
                  source_hash="a" * 64)
    first_result = post_invoice(factory, first.request())
    with factory.begin() as session:
        receipt = session.get(InventoryMovement, first_result.movement_ids[0])
        session.add(InventoryMovement(product_id=receipt.product_id,
            location_id=receipt.location_id, movement_type="reversal",
            quantity_delta=-1, posting_key="bad-reversal",
            reverses_movement_id=receipt.id))
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, second.request("second"))
    assert error.value.code == PostingErrorCode.INVALID_RECEIPT_HISTORY
    assert state(factory, second.invoice_id)[0:2] == ("approved", [])


def test_unrelated_adjustment_does_not_restore_po_capacity(database) -> None:
    _, factory = database
    first = seed(factory)
    second = seed(factory, reuse=first, number="FICT_A-INV-ADJUSTED",
                  source_hash="a" * 64)
    post_invoice(factory, first.request())
    with factory.begin() as session:
        session.add(InventoryMovement(product_id=first.product_ids[0],
            location_id=first.location_id, movement_type="adjustment",
            quantity_delta=-1, posting_key="unrelated-adjustment"))
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, second.request("adjusted"))
    assert error.value.code == PostingErrorCode.INSUFFICIENT_PO_QUANTITY
    assert state(factory, second.invoice_id)[0:2] == ("approved", [])


def test_balance_integer_overflow_rolls_back_receipts(database) -> None:
    _, factory = database
    item = seed(factory)
    with factory.begin() as session:
        session.add(InventoryBalance(product_id=item.product_ids[0],
                                     location_id=item.location_id,
                                     available_quantity=2_147_483_647))
    before = state(factory, item.invoice_id)
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request())
    assert error.value.code == PostingErrorCode.TRANSACTION_CONFLICT
    assert state(factory, item.invoice_id) == before


def test_failure_after_staged_receipt_rolls_back_and_retry_succeeds(database) -> None:
    engine, factory = database
    item = seed(factory)
    seen = 0
    def fail_second_insert(connection, cursor, statement, parameters, context, executemany):
        nonlocal seen
        if statement.startswith("INSERT INTO inventory_movements"):
            seen += 1
            if seen == 2:
                raise RuntimeError("injected second receipt failure")
    event.listen(engine, "before_cursor_execute", fail_second_insert)
    try:
        with pytest.raises(PostingConflict) as error:
            post_invoice(factory, item.request())
        assert error.value.code == PostingErrorCode.TRANSACTION_CONFLICT
    finally:
        event.remove(engine, "before_cursor_execute", fail_second_insert)
    assert state(factory, item.invoice_id) == ("approved", [], {})
    assert post_invoice(factory, item.request()).status == PostingStatus.POSTED


def test_failure_before_invoice_transition_rolls_back(database) -> None:
    engine, factory = database
    item = seed(factory)
    def fail_invoice_update(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE invoices SET"):
            raise RuntimeError("injected final transition failure")
    event.listen(engine, "before_cursor_execute", fail_invoice_update)
    try:
        with pytest.raises(PostingConflict) as error:
            post_invoice(factory, item.request())
        assert error.value.code == PostingErrorCode.TRANSACTION_CONFLICT
    finally:
        event.remove(engine, "before_cursor_execute", fail_invoice_update)
    assert state(factory, item.invoice_id) == ("approved", [], {})


def test_invoice_status_change_and_product_change_block_posting(database) -> None:
    _, factory = database
    item = seed(factory)
    with factory.begin() as session:
        session.get(Invoice, item.invoice_id).status = "validated"
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request())
    assert error.value.code == PostingErrorCode.INVALID_INVOICE_STATE
    with factory.begin() as session:
        session.get(Invoice, item.invoice_id).status = "approved"
        session.get(PurchaseOrderLine, item.po_line_ids[0]).product_id = item.product_ids[1]
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request())
    assert error.value.code == PostingErrorCode.PRODUCT_MISMATCH


def test_explicit_allocation_resolves_same_product_on_multiple_po_lines(database) -> None:
    _, factory = database
    item = seed(factory, authorize=False)
    with factory.begin() as session:
        original = session.get(PurchaseOrderLine, item.po_line_ids[0])
        additional = PurchaseOrderLine(purchase_order_id=original.purchase_order_id,
            line_number=3, product_id=original.product_id, ordered_quantity=5,
            expected_unit_cost=Decimal("4.1250"))
        session.add(additional)
        session.flush()
        second_allocation = item.allocations[0].model_copy(update={
            "purchase_order_line_id": additional.id})
    chosen = (second_allocation, item.allocations[1])
    reviewed = authorize_existing(factory, item, chosen)
    with factory() as session:
        approval = session.get(InvoicePostingAuthorization, reviewed.authorization_id)
        evidence = session.get(AuditEvent, approval.approval_event_id).after_data["posting_evidence"]
        assert evidence["resolved_po_line_rows"] == [1]
        assert evidence["reconciliation_decision"] == "needs_review"
    result = post_invoice(factory, reviewed.request())
    assert result.status == PostingStatus.POSTED
    assert {m.purchase_order_line_id for m in state(factory, item.invoice_id)[1]} == {
        additional.id, item.po_line_ids[1]}


def test_ambiguous_po_line_with_different_price_cannot_be_selected(database) -> None:
    _, factory = database
    item = seed(factory, authorize=False)
    with factory.begin() as session:
        original = session.get(PurchaseOrderLine, item.po_line_ids[0])
        additional = PurchaseOrderLine(purchase_order_id=original.purchase_order_id,
            line_number=3, product_id=original.product_id, ordered_quantity=5,
            expected_unit_cost=Decimal("99.0000"))
        session.add(additional)
        session.flush()
        wrong = item.allocations[0].model_copy(update={"purchase_order_line_id": additional.id})
    with pytest.raises(PostingConflict) as error:
        authorize_existing(factory, item, (wrong, item.allocations[1]))
    assert error.value.code == PostingErrorCode.STALE_RECONCILIATION


def test_repeated_product_rows_aggregate_to_one_po_limit(database) -> None:
    _, factory = database
    base = seed(factory, ordered_quantities=(3, 3), authorize=False)
    repeated = seed(factory, reuse=base, number="FICT_A-INV-REPEATED",
                    source_hash="a" * 64, repeat_first_line=True)
    with factory() as session:
        approval = session.get(InvoicePostingAuthorization, repeated.authorization_id)
        evidence = session.get(AuditEvent, approval.approval_event_id).after_data["posting_evidence"]
        assert evidence["resolved_validation_codes"] == ["duplicate_product_identifier"]
    assert post_invoice(factory, repeated.request("repeat")).status == PostingStatus.POSTED
    assert state(factory, repeated.invoice_id)[2] == dict(zip(base.product_ids, (3, 3), strict=True))
    with pytest.raises(PostingConflict) as error:
        seed(factory, reuse=base, number="FICT_A-INV-OVER",
             source_hash="b" * 64, repeat_first_line=True)
    assert error.value.code == PostingErrorCode.MISSING_APPROVAL_EVIDENCE
    assert state(factory, repeated.invoice_id)[2] == dict(zip(base.product_ids, (3, 3), strict=True))


def test_idempotency_key_cannot_be_reused_for_another_invoice(database) -> None:
    _, factory = database
    first = seed(factory)
    second = seed(factory, reuse=first, number="FICT_A-INV-SECOND-PO",
                  source_hash="a" * 64, new_po_reference="FICT_A-PO-SECOND")
    post_invoice(factory, first.request("shared-key"))
    before = state(factory, second.invoice_id)
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, second.request("shared-key"))
    assert error.value.code == PostingErrorCode.IDEMPOTENCY_CONFLICT
    assert state(factory, second.invoice_id) == before


def test_missing_po_and_changed_source_document_block_posting(database) -> None:
    _, factory = database
    item = seed(factory)
    with factory.begin() as session:
        extra_document = InvoiceDocument(sha256="a" * 64,
            storage_uri="memory://changed", content_type="application/pdf")
        session.add(extra_document)
        session.flush()
        session.add(InvoiceUploadAttempt(document_id=extra_document.id,
            invoice_id=item.invoice_id, original_filename="changed.pdf",
            identity_status="conflict"))
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request())
    assert error.value.code == PostingErrorCode.STALE_RECONCILIATION
    with factory.begin() as session:
        session.execute(delete(InvoiceUploadAttempt).where(
            InvoiceUploadAttempt.document_id == extra_document.id))
        session.delete(session.get(InvoiceDocument, extra_document.id))
        session.delete(session.get(PurchaseOrderLine, item.po_line_ids[0]))
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request())
    assert error.value.code == PostingErrorCode.PO_NOT_FOUND
    assert state(factory, item.invoice_id) == ("approved", [], {})


def test_other_invoice_linked_to_same_source_blocks_posting(database) -> None:
    _, factory = database
    first = seed(factory)
    second = seed(factory, reuse=first, number="FICT_A-INV-SHARED-DOC",
                  source_hash="a" * 64, new_po_reference="FICT_A-PO-SHARED-DOC")
    with factory.begin() as session:
        document_id = session.scalar(select(InvoiceDocument.id).where(
            InvoiceDocument.sha256 == "f" * 64))
        session.add(InvoiceUploadAttempt(document_id=document_id,
            invoice_id=second.invoice_id, original_filename="conflicting.pdf",
            identity_status="conflict"))
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, first.request())
    assert error.value.code == PostingErrorCode.DUPLICATE_INVOICE_IDENTITY
    assert state(factory, first.invoice_id) == ("approved", [], {})


def test_operational_database_error_rolls_back_as_typed_conflict(database) -> None:
    engine, factory = database
    item = seed(factory)
    def fail_balance(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO inventory_balances"):
            raise OperationalError(statement, parameters, RuntimeError("database unavailable"))
    event.listen(engine, "before_cursor_execute", fail_balance)
    try:
        with pytest.raises(PostingConflict) as error:
            post_invoice(factory, item.request())
        assert error.value.code == PostingErrorCode.TRANSACTION_CONFLICT
    finally:
        event.remove(engine, "before_cursor_execute", fail_balance)
    assert state(factory, item.invoice_id) == ("approved", [], {})
    assert post_invoice(factory, item.request()).status == PostingStatus.POSTED


def test_retry_rejects_corrupted_receipt_ledger(database) -> None:
    _, factory = database
    item = seed(factory)
    result = post_invoice(factory, item.request())
    with factory.begin() as session:
        session.get(InventoryMovement, result.movement_ids[0]).quantity_delta = 1
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request())
    assert error.value.code == PostingErrorCode.INVALID_RECEIPT_HISTORY


def test_new_po_line_after_approval_invalidates_reference_snapshot(database) -> None:
    _, factory = database
    item = seed(factory)
    with factory.begin() as session:
        original = session.get(PurchaseOrderLine, item.po_line_ids[0])
        session.add(PurchaseOrderLine(purchase_order_id=original.purchase_order_id,
            line_number=3, product_id=original.product_id, ordered_quantity=1,
            expected_unit_cost=original.expected_unit_cost))
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request())
    assert error.value.code == PostingErrorCode.STALE_RECONCILIATION
    assert state(factory, item.invoice_id) == ("approved", [], {})


@pytest.mark.parametrize("changed_record", ["product", "supplier"])
def test_trusted_catalog_or_supplier_change_invalidates_approval(database, changed_record) -> None:
    _, factory = database
    item = seed(factory)
    with factory.begin() as session:
        if changed_record == "product":
            session.get(Product, item.product_ids[0]).name = "Changed catalog description"
        else:
            invoice = session.get(Invoice, item.invoice_id)
            session.get(Supplier, invoice.supplier_id).name = "Changed supplier name"
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request())
    assert error.value.code == PostingErrorCode.STALE_RECONCILIATION
    assert state(factory, item.invoice_id) == ("approved", [], {})


@pytest.mark.parametrize("changed_fact", ["invoice_supplier", "product_identifier",
                                        "po_quantity", "invoice_price"])
def test_changed_approved_facts_block_posting(database, changed_fact) -> None:
    _, factory = database
    item = seed(factory)
    with factory.begin() as session:
        if changed_fact == "invoice_supplier":
            replacement = Supplier(code="REPLACEMENT", name="Replacement supplier")
            session.add(replacement)
            session.flush()
            session.get(Invoice, item.invoice_id).supplier_id = replacement.id
        elif changed_fact == "product_identifier":
            session.get(Product, item.product_ids[0]).upc_normalized = "000000000000"
        elif changed_fact == "po_quantity":
            session.get(PurchaseOrderLine, item.po_line_ids[0]).ordered_quantity += 1
        else:
            session.get(InvoiceLine, item.allocations[0].invoice_line_id).unit_cost = Decimal("5.0000")
    with pytest.raises(PostingConflict) as error:
        post_invoice(factory, item.request())
    assert error.value.code in {PostingErrorCode.STALE_RECONCILIATION,
                                PostingErrorCode.SUPPLIER_MISMATCH,
                                PostingErrorCode.PRICE_MISMATCH}
    assert state(factory, item.invoice_id) == ("approved", [], {})


def test_location_name_change_does_not_change_approved_location_identity(database) -> None:
    _, factory = database
    item = seed(factory)
    with factory.begin() as session:
        session.get(Location, item.location_id).name = "Renamed location"
    assert post_invoice(factory, item.request()).status == PostingStatus.POSTED
