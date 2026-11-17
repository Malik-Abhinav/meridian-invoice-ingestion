"""HTTP lifecycle checks against disposable PostgreSQL and local PDF storage."""

import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from meridian.api.app import create_app
from meridian.api.config import ApiSettings
from meridian.evaluation_fixtures import FixtureGroundTruth
from meridian.models import Location, Product, PurchaseOrder, PurchaseOrderLine, Supplier

ROOT = Path(__file__).resolve().parents[1]
TRUTH = FixtureGroundTruth.model_validate_json(
    (ROOT / "output/evaluation/ground_truth/fixture_a_clean_01.json").read_text())
PDF = (ROOT / "output/evaluation/original/fixture_a_clean_01.pdf").read_bytes()
SCAN = (ROOT / "output/evaluation/scanned/fixture_a_clean_01.pdf").read_bytes()
HEADERS = {"Authorization": "Bearer test-staff-token"}


@pytest.fixture
def api_db(tmp_path):
    source = os.environ.get("DATABASE_URL")
    if not source or make_url(source).get_backend_name() != "postgresql":
        pytest.skip("Disposable PostgreSQL database is required")
    parsed = make_url(source)
    name = f"meridian_api_{uuid4().hex[:16]}"
    admin = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"CREATE DATABASE {name}"))
    url = parsed.set(database=name).render_as_string(hide_password=False)
    try:
        with patch.dict(os.environ, {"DATABASE_URL": url}):
            command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        engine = create_engine(url, isolation_level="READ COMMITTED")
        factory = sessionmaker(engine, expire_on_commit=False)
        try:
            visible = TRUTH.displayed_invoice
            with factory.begin() as session:
                supplier = Supplier(code="FICT_A", name=visible.supplier.name)
                location = Location(code="MAIN", name="Main inventory")
                session.add_all((supplier, location))
                session.flush()
                products = {}
                for item in visible.products:
                    product = Product(name=item.description, upc_normalized=item.upc,
                                      ndc_normalized=item.ndc)
                    session.add(product)
                    session.flush()
                    products[item.sku] = product
                po = PurchaseOrder(supplier_id=supplier.id,
                                   order_number=visible.purchase_order_reference,
                                   status="open")
                session.add(po)
                session.flush()
                for index, line in enumerate(visible.lines, 1):
                    session.add(PurchaseOrderLine(purchase_order_id=po.id,
                        line_number=index, product_id=products[line.product_sku].id,
                        ordered_quantity=line.quantity, expected_unit_cost=line.unit_cost))
            app = create_app(ApiSettings(storage_root=tmp_path / "storage",
                dev_auth_enabled=True, dev_staff_token="test-staff-token"), factory)
            with TestClient(app, raise_server_exceptions=False) as client:
                yield client, factory, location.id
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))
        admin.dispose()


def test_http_process_smoke(api_db):
    client, _, _ = api_db
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}
    uploaded = client.post("/api/v1/invoices/upload", headers=HEADERS,
        files={"file": ("invoice.pdf", PDF, "application/pdf")})
    assert uploaded.status_code == 201, uploaded.text
    upload_id = uploaded.json()["upload_attempt_id"]
    processed = client.post(f"/api/v1/uploads/{upload_id}/process", headers=HEADERS)
    assert processed.status_code == 200, processed.text
    assert processed.json()["status"] == "completed"
    assert processed.json()["invoice_id"] is not None, processed.json()


def test_scanned_invoice_staff_correction_approval_and_posting(api_db):
    client, factory, location_id = api_db
    uploaded = client.post("/api/v1/invoices/upload", headers=HEADERS,
        files={"file": ("scan.pdf", SCAN, "application/pdf")})
    processed = client.post(f"/api/v1/uploads/{uploaded.json()['upload_attempt_id']}/process", headers=HEADERS)
    assert processed.status_code == 200, processed.text
    invoice_id = processed.json()["invoice_id"]
    assert processed.json()["validation"]["decision"] == "needs_review"
    review = client.get(f"/api/v1/invoices/{invoice_id}/review", headers=HEADERS)
    assert review.status_code == 200, review.text
    assert review.json()["authorization_ready"] is False
    assert review.json()["original_extraction"]["invoice"]["lines"][1]["quantity"]["value"] == 53
    correction = client.patch(f"/api/v1/invoices/{invoice_id}", headers=HEADERS,
        json={"expected_revision": 1, "lines": [{"source_row_order": 2, "quantity": 3}]})
    assert correction.status_code == 200, correction.text
    assert correction.json()["revision"] == 2
    review = client.get(f"/api/v1/invoices/{invoice_id}/review", headers=HEADERS).json()
    assert review["original_extraction"]["invoice"]["lines"][1]["quantity"]["value"] == 53
    assert review["current_extraction"]["invoice"]["lines"][1]["quantity"]["value"] == 3
    original = client.get(f"/api/v1/invoices/{invoice_id}/processing", headers=HEADERS).json()["items"][0]
    assert original["extraction"]["invoice"]["lines"][1]["quantity"]["value"] == 53
    assert original["ocr"] == processed.json()["ocr"]
    assert review["authorization_ready"] is True
    matches = {item["source_row_order"]: item["po_line_ids"]
               for item in review["reconciliation"]["product_matches"]}
    allocations = [{"invoice_line_id": line["id"],
                    "purchase_order_line_id": matches[index][0],
                    "source_row_order": index}
                   for index, line in enumerate(review["invoice"]["lines"], 1)]
    approval = client.post(f"/api/v1/invoices/{invoice_id}/approve", headers=HEADERS,
        json={"expected_revision": 2, "location_id": str(location_id),
              "allocations": allocations})
    assert approval.status_code == 200, approval.text
    first = client.post(f"/api/v1/invoices/{invoice_id}/post", headers=HEADERS,
        json={"idempotency_key": "http-post-1"})
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "posted"
    again = client.post(f"/api/v1/invoices/{invoice_id}/post", headers=HEADERS,
        json={"idempotency_key": "http-post-1"})
    assert again.status_code == 200 and again.json()["status"] == "already_posted", again.text
    assert first.json()["movement_ids"] == again.json()["movement_ids"]
    movements = client.get(f"/api/v1/invoices/{invoice_id}/movements", headers=HEADERS)
    assert movements.status_code == 200
    assert len(movements.json()["items"]) == 2
    assert sorted(item["quantity_delta"] for item in movements.json()["items"]) == [2, 3]


def upload_and_process(client, pdf=PDF, name="invoice.pdf"):
    uploaded = client.post("/api/v1/invoices/upload", headers=HEADERS,
        files={"file": (name, pdf, "application/pdf")})
    assert uploaded.status_code == 201, uploaded.text
    processed = client.post(f"/api/v1/uploads/{uploaded.json()['upload_attempt_id']}/process",
                            headers=HEADERS)
    assert processed.status_code == 200, processed.text
    return uploaded.json(), processed.json()


def test_upload_validation_auth_and_deduplication(api_db):
    client, _, _ = api_db
    assert client.get("/health").status_code == 200
    assert client.post("/api/v1/invoices/upload",
        files={"file": ("invoice.pdf", PDF, "application/pdf")}).status_code == 401
    for name, body, expected in (("not.pdf", b"hello", 400),
                                 ("bad.pdf", b"%PDF-broken", 400),
                                 ("../invoice.pdf", PDF, 400)):
        response = client.post("/api/v1/invoices/upload", headers=HEADERS,
            files={"file": (name, body, "application/pdf")})
        assert response.status_code == expected, response.text
        assert "correlation_id" in response.json()["error"]
    first = client.post("/api/v1/invoices/upload", headers=HEADERS,
        files={"file": ("invoice.pdf", PDF, "application/pdf")})
    second = client.post("/api/v1/invoices/upload", headers=HEADERS,
        files={"file": ("again.pdf", PDF, "application/pdf")})
    assert first.status_code == second.status_code == 201
    assert first.json()["document_id"] == second.json()["document_id"]
    assert first.json()["upload_attempt_id"] != second.json()["upload_attempt_id"]
    assert first.json()["source_sha256"] == second.json()["source_sha256"]


def test_upload_size_limit_and_invalid_uuid(api_db):
    client, factory, _ = api_db
    small = create_app(ApiSettings(storage_root=client.app.state.settings.storage_root,
        max_upload_bytes=100, dev_auth_enabled=True,
        dev_staff_token="test-staff-token"), factory)
    with TestClient(small, raise_server_exceptions=False) as limited:
        response = limited.post("/api/v1/invoices/upload", headers=HEADERS,
            files={"file": ("invoice.pdf", PDF, "application/pdf")})
        assert response.status_code == 413
    response = client.get("/api/v1/invoices/not-a-uuid", headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert client.get(f"/api/v1/invoices/{uuid4()}", headers=HEADERS).status_code == 404


def test_import_health_and_fail_closed_auth_without_database(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app(ApiSettings(storage_root=tmp_path / "storage"))
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").status_code == 503
        assert client.get("/api/v1/invoices").status_code == 401
        assert client.get("/api/v1/invoices", headers={"Authorization": "Bearer fake"}).status_code == 401
        assert client.get("/not-a-route").json()["error"]["code"] == "not_found"
        app.state.staff_authenticator = lambda token: None
        assert client.get("/api/v1/invoices", headers={"Authorization": "Bearer fake"}).status_code == 403


def test_processing_reuse_reconstruction_listing_and_document(api_db):
    client, factory, _ = api_db
    uploaded, processed = upload_and_process(client)
    invoice_id = processed["invoice_id"]
    again = client.post(f"/api/v1/uploads/{uploaded['upload_attempt_id']}/process",
                        headers=HEADERS)
    assert again.status_code == 200
    assert again.json()["id"] == processed["id"]
    assert again.json()["attempt_count"] == 1
    by_invoice = client.post(f"/api/v1/invoices/{invoice_id}/process", headers=HEADERS)
    assert by_invoice.status_code == 200 and by_invoice.json()["id"] == processed["id"]
    second_upload = client.post("/api/v1/invoices/upload", headers=HEADERS,
        files={"file": ("duplicate.pdf", PDF, "application/pdf")})
    assert second_upload.json()["identity_status"] == "duplicate"
    reused = client.post(f"/api/v1/uploads/{second_upload.json()['upload_attempt_id']}/process",
                         headers=HEADERS)
    assert reused.json()["id"] == processed["id"]
    assert reused.json()["invoice_id"] == invoice_id
    filtered = client.get("/api/v1/invoices", headers=HEADERS,
        params={"limit": 1, "offset": 0, "status": "validated",
                "supplier_id": client.get(f"/api/v1/invoices/{invoice_id}", headers=HEADERS).json()["supplier_id"]})
    assert filtered.json()["pagination"] == {"limit": 1, "offset": 0, "total": 1}
    assert len(filtered.json()["items"]) == 1
    assert isinstance(filtered.json()["items"][0]["total"], str)
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/api/v1/invoices/{invoice_id}/review"]["get"]["responses"]["200"][
        "content"]["application/json"]["schema"]["$ref"].endswith("/ReviewResponse")
    assert client.get("/api/v1/invoices", headers=HEADERS,
        params={"status": "posted"}).json()["pagination"]["total"] == 0
    document = client.get(f"/api/v1/invoices/{invoice_id}/document", headers=HEADERS)
    assert document.status_code == 200 and document.content == PDF
    assert document.headers["x-content-type-options"] == "nosniff"
    processing = client.get(f"/api/v1/invoices/{invoice_id}/processing", headers=HEADERS)
    assert processing.json()["items"][0]["ocr"]["source_sha256"] == uploaded["source_sha256"]
    assert processing.json()["items"][0]["extraction"]["source_sha256"] == uploaded["source_sha256"]
    other = create_app(client.app.state.settings, factory)
    with TestClient(other, raise_server_exceptions=False) as restarted:
        assert restarted.get(f"/api/v1/invoices/{invoice_id}/review", headers=HEADERS).json()[
            "original_extraction"] == processed["extraction"]


def test_missing_optional_provider_credentials_fail_closed(api_db, monkeypatch):
    client, factory, _ = api_db
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    other = create_app(ApiSettings(storage_root=client.app.state.settings.storage_root,
        provider="openai", dev_auth_enabled=True,
        dev_staff_token="test-staff-token"), factory)
    uploaded = client.post("/api/v1/invoices/upload", headers=HEADERS,
        files={"file": ("invoice.pdf", PDF, "application/pdf")}).json()
    with TestClient(other, raise_server_exceptions=False) as configured:
        response = configured.post(f"/api/v1/uploads/{uploaded['upload_attempt_id']}/process",
                                   headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "provider_not_configured"


def test_processing_failure_retry_preserves_ocr(api_db):
    from meridian.extraction import ProviderError
    client, factory, _ = api_db
    upload = client.post("/api/v1/invoices/upload", headers=HEADERS,
        files={"file": ("invoice.pdf", PDF, "application/pdf")}).json()
    with patch("meridian.api.processing.extract_invoice",
               side_effect=ProviderError("temporary provider failure", retryable=True)):
        failed = client.post(f"/api/v1/uploads/{upload['upload_attempt_id']}/process", headers=HEADERS)
    assert failed.status_code == 503
    snapshot = client.get(f"/api/v1/uploads/{upload['upload_attempt_id']}/processing",
                          headers=HEADERS).json()["items"][0]
    assert snapshot["status"] == "failed" and snapshot["ocr"] is not None
    assert snapshot["retryable"] is True and snapshot["attempt_count"] == 1
    with patch("meridian.api.processing.extract_ocr", side_effect=AssertionError("OCR reran")):
        recovered = client.post(f"/api/v1/uploads/{upload['upload_attempt_id']}/process",
                                headers=HEADERS)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["attempt_count"] == 2
    assert recovered.json()["status"] == "completed"


def test_identity_conflict_keeps_existing_invoice_unchanged(api_db):
    client, _, _ = api_db
    _, original = upload_and_process(client, PDF)
    uploaded = client.post("/api/v1/invoices/upload", headers=HEADERS,
        files={"file": ("scan.pdf", SCAN, "application/pdf")}).json()
    processed = client.post(f"/api/v1/uploads/{uploaded['upload_attempt_id']}/process",
                            headers=HEADERS)
    assert processed.status_code == 200
    assert processed.json()["invoice_id"] is None
    assert processed.json()["upload_identity_status"] == "conflict"
    assert client.get(f"/api/v1/invoices/{original['invoice_id']}", headers=HEADERS).json()["status"] == "validated"
    assert client.get("/api/v1/invoices", headers=HEADERS).json()["pagination"]["total"] == 1


def test_correction_revision_audit_and_approval_blocks(api_db):
    client, _, location_id = api_db
    _, processed = upload_and_process(client, SCAN, "scan.pdf")
    invoice_id = processed["invoice_id"]
    assert client.post(f"/api/v1/invoices/{invoice_id}/approve",
        json={"expected_revision": 1, "location_id": str(location_id), "allocations": []}).status_code == 401
    assert client.post(f"/api/v1/invoices/{invoice_id}/post", headers=HEADERS,
        json={"idempotency_key": "not-approved"}).status_code == 409
    review = client.get(f"/api/v1/invoices/{invoice_id}/review", headers=HEADERS).json()
    matches = {row["source_row_order"]: row["po_line_ids"][0]
               for row in review["reconciliation"]["product_matches"]}
    allocations = [{"invoice_line_id": line["id"],
                    "purchase_order_line_id": matches[index], "source_row_order": index}
                   for index, line in enumerate(review["invoice"]["lines"], 1)]
    blocked = client.post(f"/api/v1/invoices/{invoice_id}/approve", headers=HEADERS,
        json={"expected_revision": 1, "location_id": str(location_id), "allocations": allocations})
    assert blocked.status_code == 409
    corrected = client.patch(f"/api/v1/invoices/{invoice_id}", headers=HEADERS,
        json={"expected_revision": 1, "lines": [{"source_row_order": 2, "quantity": 3}]})
    assert corrected.status_code == 200
    assert client.patch(f"/api/v1/invoices/{invoice_id}", headers=HEADERS,
        json={"expected_revision": 1, "invoice_number": "FICT_A-INV-OTHER"}).status_code == 409
    audit = client.get(f"/api/v1/invoices/{invoice_id}/audit", headers=HEADERS).json()["items"]
    assert [item["action"] for item in audit] == ["process", "correct"]
    assert audit[-1]["actor_id"] == "local-reviewer"
    assert audit[-1]["before"]["extraction"]["invoice"]["lines"][1]["quantity"]["value"] == 53


def test_reject_and_audit(api_db):
    client, _, _ = api_db
    _, processed = upload_and_process(client, SCAN, "scan.pdf")
    invoice_id = processed["invoice_id"]
    rejected = client.post(f"/api/v1/invoices/{invoice_id}/reject", headers=HEADERS,
        json={"expected_revision": 1, "reason": "Reviewer rejected source document"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert client.get(f"/api/v1/invoices/{invoice_id}/audit", headers=HEADERS).json()["items"][-1]["action"] == "reject"
    assert client.patch(f"/api/v1/invoices/{invoice_id}", headers=HEADERS,
        json={"expected_revision": 1, "invoice_number": "NEW"}).status_code == 409


def test_concurrent_duplicate_upload_uses_one_document(api_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    client, _, _ = api_db
    barrier = Barrier(2)
    def worker(index):
        barrier.wait()
        return client.post("/api/v1/invoices/upload", headers=HEADERS,
            files={"file": (f"invoice-{index}.pdf", PDF, "application/pdf")})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(worker, (1, 2)))
    assert first.status_code == second.status_code == 201
    assert first.json()["document_id"] == second.json()["document_id"]
    assert first.json()["upload_attempt_id"] != second.json()["upload_attempt_id"]


def approve_original(client, location_id):
    _, processed = upload_and_process(client)
    invoice_id = processed["invoice_id"]
    review = client.get(f"/api/v1/invoices/{invoice_id}/review", headers=HEADERS).json()
    matches = {item["source_row_order"]: item["po_line_ids"][0]
               for item in review["reconciliation"]["product_matches"]}
    allocations = [{"invoice_line_id": line["id"],
                    "purchase_order_line_id": matches[index], "source_row_order": index}
                   for index, line in enumerate(review["invoice"]["lines"], 1)]
    approved = client.post(f"/api/v1/invoices/{invoice_id}/approve", headers=HEADERS,
        json={"expected_revision": 1, "location_id": str(location_id),
              "allocations": allocations})
    assert approved.status_code == 200, approved.text
    return invoice_id


def test_posting_http_conflicts_and_atomic_audit(api_db):
    from meridian.models import InventoryBalance, InventoryMovement, Invoice, PurchaseOrder, PurchaseOrderLine
    from sqlalchemy import select
    client, factory, location_id = api_db
    invoice_id = approve_original(client, location_id)
    unauthorized = client.post(f"/api/v1/invoices/{invoice_id}/post",
        json={"idempotency_key": "unauthorized"})
    assert unauthorized.status_code == 401
    with factory() as session:
        assert list(session.scalars(select(InventoryMovement))) == []
        assert list(session.scalars(select(InventoryBalance))) == []
    with factory.begin() as session:
        po_line = session.scalar(select(PurchaseOrderLine).order_by(PurchaseOrderLine.line_number).limit(1))
        po = session.get(PurchaseOrder, po_line.purchase_order_id)
        po.status = "closed"
    blocked = client.post(f"/api/v1/invoices/{invoice_id}/post", headers=HEADERS,
        json={"idempotency_key": "closed-po"})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "po_ineligible"
    with factory() as session:
        assert session.get(Invoice, invoice_id).status == "approved"
        assert list(session.scalars(select(InventoryMovement))) == []
        assert list(session.scalars(select(InventoryBalance))) == []
    with factory.begin() as session:
        session.get(PurchaseOrder, po.id).status = "open"
    posted = client.post(f"/api/v1/invoices/{invoice_id}/post", headers=HEADERS,
        json={"idempotency_key": "http-key"})
    assert posted.status_code == 200 and posted.json()["status"] == "posted", posted.text
    retry = client.post(f"/api/v1/invoices/{invoice_id}/post", headers=HEADERS,
        json={"idempotency_key": "http-key"})
    assert retry.status_code == 200 and retry.json()["status"] == "already_posted"
    assert posted.json()["movement_ids"] == retry.json()["movement_ids"]
    changed = client.post(f"/api/v1/invoices/{invoice_id}/post", headers=HEADERS,
        json={"idempotency_key": "other-key"})
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "idempotency_conflict"
    audit = client.get(f"/api/v1/invoices/{invoice_id}/audit", headers=HEADERS).json()["items"]
    assert [item["action"] for item in audit] == ["process", "approve", "post"]
    assert audit[-1]["actor_id"] == "local-reviewer"
    assert len(audit[-1]["after"]["movement_ids"]) == 2
    assert client.patch(f"/api/v1/invoices/{invoice_id}", headers=HEADERS,
        json={"expected_revision": 1, "invoice_number": "CHANGED"}).status_code == 409


def test_concurrent_http_posting_has_one_receipt_set(api_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from meridian.models import InventoryMovement
    from sqlalchemy import select
    client, factory, location_id = api_db
    invoice_id = approve_original(client, location_id)
    gate = Barrier(2)
    def post():
        gate.wait()
        return client.post(f"/api/v1/invoices/{invoice_id}/post", headers=HEADERS,
            json={"idempotency_key": "same-concurrent-key"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: post(), range(2)))
    assert first.status_code == second.status_code == 200
    assert {first.json()["status"], second.json()["status"]} == {"posted", "already_posted"}
    with factory() as session:
        assert len(list(session.scalars(select(InventoryMovement)))) == 2


def test_concurrent_processing_claim_blocks_duplicate_provider_call(api_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from meridian.api import processing as processing_module
    client, _, _ = api_db
    uploaded = client.post("/api/v1/invoices/upload", headers=HEADERS,
        files={"file": ("invoice.pdf", PDF, "application/pdf")}).json()
    duplicate_upload = client.post("/api/v1/invoices/upload", headers=HEADERS,
        files={"file": ("duplicate.pdf", PDF, "application/pdf")}).json()
    started, release = Event(), Event()
    original = processing_module.extract_invoice
    def delayed(*args, **kwargs):
        started.set()
        assert release.wait(timeout=10)
        return original(*args, **kwargs)
    with patch("meridian.api.processing.extract_invoice", side_effect=delayed) as provider:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(lambda: client.post(
                f"/api/v1/uploads/{uploaded['upload_attempt_id']}/process", headers=HEADERS))
            assert started.wait(timeout=10)
            duplicate = client.post(
                f"/api/v1/uploads/{duplicate_upload['upload_attempt_id']}/process", headers=HEADERS)
            assert duplicate.status_code == 409
            assert duplicate.json()["error"]["code"] == "processing_in_progress"
            release.set()
            completed = future.result(timeout=10)
    assert completed.status_code == 200
    assert provider.call_count == 1
    reused = client.post(f"/api/v1/uploads/{duplicate_upload['upload_attempt_id']}/process",
                         headers=HEADERS)
    assert reused.status_code == 200
    assert reused.json()["id"] == completed.json()["id"]


def test_stale_processing_lease_can_be_reclaimed(api_db):
    from datetime import datetime, timedelta, timezone
    from meridian.api.processing import config_fingerprint
    from meridian.models import InvoiceProcessingAttempt
    client, factory, _ = api_db
    upload = client.post("/api/v1/invoices/upload", headers=HEADERS,
        files={"file": ("invoice.pdf", PDF, "application/pdf")}).json()
    with factory.begin() as session:
        session.add(InvoiceProcessingAttempt(document_id=upload["document_id"],
            upload_attempt_id=upload["upload_attempt_id"],
            config_fingerprint=config_fingerprint(client.app.state.settings),
            source_sha256=upload["source_sha256"], status="running",
            lease_token=uuid4(), attempt_count=1, provider="mock", model_name="ocr-only-v1",
            updated_at=datetime.now(timezone.utc) - timedelta(hours=1)))
    recovered = client.post(f"/api/v1/uploads/{upload['upload_attempt_id']}/process",
                            headers=HEADERS)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["attempt_count"] == 2


def test_controlled_header_line_corrections_and_exact_money(api_db):
    client, _, location_id = api_db
    _, processed = upload_and_process(client)
    invoice_id = processed["invoice_id"]
    review = client.get(f"/api/v1/invoices/{invoice_id}/review", headers=HEADERS).json()
    current = review["current_extraction"]["invoice"]
    first = current["lines"][0]
    body = {"expected_revision": 1,
        "supplier_id": review["invoice"]["supplier_id"],
        "invoice_number": current["invoice_number"]["value"],
        "invoice_date": current["invoice_date"]["value"],
        "purchase_order_reference": current["purchase_order_reference"]["value"],
        "printed_subtotal": current["printed_subtotal"]["value"],
        "printed_tax": current["printed_tax"]["value"],
        "printed_total": current["printed_total"]["value"],
        "lines": [{"source_row_order": 1,
                   "product_id": review["invoice"]["lines"][0]["product_id"],
                   "upc": first["upc"]["value"],
                   "quantity": first["quantity"]["value"],
                   "unit_cost": first["unit_cost"]["value"],
                   "printed_line_total": first["printed_line_total"]["value"]}]}
    corrected = client.patch(f"/api/v1/invoices/{invoice_id}", headers=HEADERS, json=body)
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["revision"] == 2
    assert corrected.json()["validation"]["decision"] == "needs_review"
    assert corrected.json()["reconciliation"]["findings"] == []
    assert "line[1].product_id" in corrected.json()["corrected_fields"]
    invalid = client.patch(f"/api/v1/invoices/{invoice_id}", headers=HEADERS,
        json={"expected_revision": 2,
              "lines": [{"source_row_order": 1, "unit_cost": 4.125}]})
    assert invalid.status_code == 422
    invalid_qty = client.patch(f"/api/v1/invoices/{invoice_id}", headers=HEADERS,
        json={"expected_revision": 2,
              "lines": [{"source_row_order": 1, "quantity": 0}]})
    assert invalid_qty.status_code == 422
    review = client.get(f"/api/v1/invoices/{invoice_id}/review", headers=HEADERS).json()
    matches = {item["source_row_order"]: item["po_line_ids"][0]
               for item in review["reconciliation"]["product_matches"]}
    allocations = [{"invoice_line_id": line["id"],
                    "purchase_order_line_id": matches[index], "source_row_order": index}
                   for index, line in enumerate(review["invoice"]["lines"], 1)]
    approved = client.post(f"/api/v1/invoices/{invoice_id}/approve", headers=HEADERS,
        json={"expected_revision": 2, "location_id": str(location_id),
              "allocations": allocations})
    assert approved.status_code == 200, approved.text
    assert "missing_evidence" in approved.json()["evidence"]["resolved_validation_codes"]


def test_sensitive_routes_require_verified_staff(api_db):
    client, _, location_id = api_db
    upload, processed = upload_and_process(client)
    invoice_id = processed["invoice_id"]
    cases = [
        ("get", f"/api/v1/invoices/{invoice_id}", None),
        ("get", f"/api/v1/invoices/{invoice_id}/processing", None),
        ("get", f"/api/v1/invoices/{invoice_id}/audit", None),
        ("get", f"/api/v1/invoices/{invoice_id}/document", None),
        ("get", f"/api/v1/invoices/{invoice_id}/review", None),
        ("get", f"/api/v1/invoices/{invoice_id}/movements", None),
        ("get", f"/api/v1/uploads/{upload['upload_attempt_id']}/processing", None),
        ("post", f"/api/v1/uploads/{upload['upload_attempt_id']}/process", None),
        ("post", f"/api/v1/invoices/{invoice_id}/process", None),
        ("patch", f"/api/v1/invoices/{invoice_id}",
         {"expected_revision": 1, "invoice_number": "FICT_A-INV-0001"}),
        ("post", f"/api/v1/invoices/{invoice_id}/approve",
         {"expected_revision": 1, "location_id": str(location_id), "allocations": []}),
        ("post", f"/api/v1/invoices/{invoice_id}/reject",
         {"expected_revision": 1, "reason": "staff review"}),
        ("post", f"/api/v1/invoices/{invoice_id}/post",
         {"idempotency_key": "unapproved"}),
    ]
    for method, path, body in cases:
        call = getattr(client, method)
        response = call(path, json=body) if body is not None else call(path)
        assert response.status_code == 401, (method, path, response.text)
        response = call(path, headers={"Authorization": "Bearer wrong-secret"},
                        json=body) if body is not None else call(path, headers={
                            "Authorization": "Bearer wrong-secret"})
        assert response.status_code == 401, (method, path, response.text)
        assert "wrong-secret" not in response.text
    assert client.get("/api/v1/invoices", headers={"X-Staff-ID": "forged"}).status_code == 401
    assert client.get("/openapi.json").json()["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http", "scheme": "bearer"}
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/invoices/{invoice_id}/post"]["post"]
    assert {"HTTPBearer": []} in operation["security"]


def test_expired_development_token_is_rejected(api_db):
    from datetime import datetime, timedelta, timezone
    client, factory, _ = api_db
    app = create_app(ApiSettings(storage_root=client.app.state.settings.storage_root,
        dev_auth_enabled=True, dev_staff_token="expired-token",
        dev_token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)), factory)
    with TestClient(app, raise_server_exceptions=False) as expired:
        response = expired.get("/api/v1/invoices", headers={"Authorization": "Bearer expired-token"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "credentials_expired"


def test_multipart_stream_limit_runs_before_parser(api_db):
    client, factory, _ = api_db
    app = create_app(ApiSettings(storage_root=client.app.state.settings.storage_root,
        max_upload_bytes=100, dev_auth_enabled=True,
        dev_staff_token="test-staff-token"), factory)
    boundary = "stream-limit"
    def body_chunks():
        yield f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"large.pdf\"\r\n\r\n".encode()
        yield b"%PDF-" + b"x" * (70 * 1024)
        yield f"\r\n--{boundary}--\r\n".encode()
    with TestClient(app, raise_server_exceptions=False) as limited:
        with patch.object(limited.app.state.storage, "store", side_effect=AssertionError("parser reached storage")):
            response = limited.post("/api/v1/invoices/upload", headers=HEADERS | {
                "Content-Type": f"multipart/form-data; boundary={boundary}"},
                content=body_chunks())
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "upload_too_large"
    assert response.json()["error"]["correlation_id"] == response.headers["x-correlation-id"]


def test_document_storage_rejects_symlink_escape(tmp_path):
    import asyncio
    import hashlib
    from io import BytesIO
    from fastapi import UploadFile
    from meridian.api.storage import LocalPdfStorage, StorageError
    digest = hashlib.sha256(PDF).hexdigest()
    root = tmp_path / "storage"
    external = tmp_path / "external"
    external.mkdir()
    external_pdf = external / "source.pdf"
    external_pdf.write_bytes(PDF)
    root.mkdir()
    (root / digest[:2]).symlink_to(external, target_is_directory=True)
    storage = LocalPdfStorage(root)
    with pytest.raises(StorageError, match="symbolic link"):
        storage.read(digest)
    with pytest.raises(StorageError, match="symbolic link"):
        asyncio.run(storage.store(UploadFile(file=BytesIO(PDF), filename="invoice.pdf"),
                                  max_bytes=len(PDF), max_pages=30))
    (root / digest[:2]).unlink()
    (root / digest[:2]).mkdir()
    (root / digest[:2] / f"{digest}.pdf").symlink_to(external_pdf)
    with pytest.raises(StorageError, match="symbolic link"):
        storage.read(digest)


def test_concurrent_corrections_one_revision_wins(api_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    client, _, _ = api_db
    _, processed = upload_and_process(client, SCAN, "scan.pdf")
    invoice_id = processed["invoice_id"]
    gate = Barrier(2)

    def correct(quantity):
        gate.wait()
        return client.patch(f"/api/v1/invoices/{invoice_id}", headers=HEADERS,
            json={"expected_revision": 1,
                  "lines": [{"source_row_order": 2, "quantity": quantity}]})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(correct, 3)
        second = pool.submit(correct, 4)
        responses = [first.result(timeout=15), second.result(timeout=15)]
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert next(response for response in responses if response.status_code == 409).json()[
        "error"]["code"] == "revision_conflict"
    review = client.get(f"/api/v1/invoices/{invoice_id}/review", headers=HEADERS).json()
    assert review["invoice"]["revision"] == 2
    assert review["current_extraction"]["invoice"]["lines"][1]["quantity"]["value"] in (3, 4)
    assert review["original_extraction"]["invoice"]["lines"][1]["quantity"]["value"] == 53


def test_stale_worker_cannot_overwrite_reclaimed_processing(api_db):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta, timezone
    from threading import Event, Lock
    from sqlalchemy import select
    from meridian.api import processing as processing_module
    from meridian.models import InvoiceProcessingAttempt
    client, factory, _ = api_db
    upload = client.post("/api/v1/invoices/upload", headers=HEADERS,
        files={"file": ("invoice.pdf", PDF, "application/pdf")}).json()
    started, release, counter_lock = Event(), Event(), Lock()
    calls = 0
    original = processing_module.extract_invoice

    def delayed(*args, **kwargs):
        nonlocal calls
        with counter_lock:
            calls += 1
            call = calls
        if call == 1:
            started.set()
            assert release.wait(timeout=15)
        return original(*args, **kwargs)

    with patch("meridian.api.processing.extract_invoice", side_effect=delayed):
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(lambda: client.post(
                f"/api/v1/uploads/{upload['upload_attempt_id']}/process", headers=HEADERS))
            try:
                assert started.wait(timeout=15)
                with factory.begin() as session:
                    run = session.scalar(select(InvoiceProcessingAttempt))
                    assert run.status == "running"
                    run.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
                second = client.post(
                    f"/api/v1/uploads/{upload['upload_attempt_id']}/process", headers=HEADERS)
                assert second.status_code == 200, second.text
            finally:
                release.set()
            stale = first.result(timeout=15)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_processing"
    with factory() as session:
        run = session.scalar(select(InvoiceProcessingAttempt))
        assert run.status == "completed"
        assert run.attempt_count == 2
        assert str(run.invoice_id) == second.json()["invoice_id"]
    assert calls == 2


def test_failed_final_processing_transaction_leaves_no_invoice(api_db):
    from sqlalchemy import select
    from meridian.models import Invoice, InvoiceProcessingAttempt
    client, factory, _ = api_db
    upload = client.post("/api/v1/invoices/upload", headers=HEADERS,
        files={"file": ("invoice.pdf", PDF, "application/pdf")}).json()
    with patch("meridian.api.processing._link_invoice", side_effect=RuntimeError("test failure")):
        failed = client.post(f"/api/v1/uploads/{upload['upload_attempt_id']}/process",
                             headers=HEADERS)
    assert failed.status_code == 500
    assert "test failure" not in failed.text
    with factory() as session:
        assert list(session.scalars(select(Invoice))) == []
        run = session.scalar(select(InvoiceProcessingAttempt))
        assert run.status == "failed"
        assert run.retryable is True
        assert run.invoice_id is None
