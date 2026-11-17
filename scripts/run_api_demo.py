"""Disposable HTTP demo using the fictional scanned A invoice and an explicit staff correction."""

import json
import os
import secrets
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from meridian.api.app import create_app
from meridian.api.config import ApiSettings
from meridian.models import InventoryBalance, Location, Product, PurchaseOrder, PurchaseOrderLine, Supplier

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_api import TRUTH, SCAN  # noqa: E402 - disposable demo inputs only


def main() -> None:
    source = os.environ.get("DATABASE_URL")
    if not source or make_url(source).get_backend_name() != "postgresql":
        raise RuntimeError("A local PostgreSQL DATABASE_URL is required")
    parsed = make_url(source)
    name = f"meridian_http_demo_{uuid4().hex[:16]}"
    admin = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"CREATE DATABASE {name}"))
    url = parsed.set(database=name).render_as_string(hide_password=False)
    try:
        with patch.dict(os.environ, {"DATABASE_URL": url}):
            command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        engine = create_engine(url, isolation_level="READ COMMITTED")
        try:
            factory = sessionmaker(engine, expire_on_commit=False)
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
                                   order_number=visible.purchase_order_reference, status="open")
                session.add(po)
                session.flush()
                for index, line in enumerate(visible.lines, 1):
                    session.add(PurchaseOrderLine(purchase_order_id=po.id, line_number=index,
                        product_id=products[line.product_sku].id,
                        ordered_quantity=line.quantity, expected_unit_cost=line.unit_cost))
                location_id = location.id
            with TemporaryDirectory(prefix="meridian-http-demo-") as storage:
                demo_token = secrets.token_urlsafe(32)
                app = create_app(ApiSettings(storage_root=Path(storage), dev_auth_enabled=True,
                    dev_staff_token=demo_token), factory)
                headers = {"Authorization": f"Bearer {demo_token}"}
                with TestClient(app, raise_server_exceptions=False) as client:
                    def expect(response):
                        if response.status_code >= 400:
                            raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
                        return response.json()
                    uploaded = expect(client.post("/api/v1/invoices/upload", headers=headers,
                        files={"file": ("fictional-scan.pdf", SCAN, "application/pdf")}))
                    processed = expect(client.post(
                        f"/api/v1/uploads/{uploaded['upload_attempt_id']}/process", headers=headers))
                    invoice_id = processed["invoice_id"]
                    initial_findings = [item["code"] for item in processed["validation"]["findings"]]
                    correction = expect(client.patch(f"/api/v1/invoices/{invoice_id}", headers=headers,
                        json={"expected_revision": 1,
                              "lines": [{"source_row_order": 2, "quantity": 3}]}))
                    review = expect(client.get(f"/api/v1/invoices/{invoice_id}/review", headers=headers))
                    original = expect(client.get(
                        f"/api/v1/invoices/{invoice_id}/processing", headers=headers))["items"][0]
                    assert original["ocr"] == processed["ocr"]
                    assert original["extraction"]["invoice"]["lines"][1]["quantity"]["value"] == 53
                    assert review["current_extraction"]["invoice"]["lines"][1]["quantity"]["value"] == 3
                    matches = {row["source_row_order"]: row["po_line_ids"][0]
                               for row in review["reconciliation"]["product_matches"]}
                    allocations = [{"invoice_line_id": line["id"],
                                    "purchase_order_line_id": matches[index],
                                    "source_row_order": index}
                                   for index, line in enumerate(review["invoice"]["lines"], 1)]
                    approval = expect(client.post(f"/api/v1/invoices/{invoice_id}/approve",
                        headers=headers, json={"expected_revision": correction["revision"],
                            "location_id": str(location_id), "allocations": allocations}))
                    first = expect(client.post(f"/api/v1/invoices/{invoice_id}/post", headers=headers,
                        json={"idempotency_key": "demo-post-once"}))
                    retry = expect(client.post(f"/api/v1/invoices/{invoice_id}/post", headers=headers,
                        json={"idempotency_key": "demo-post-once"}))
                    audit = expect(client.get(f"/api/v1/invoices/{invoice_id}/audit", headers=headers))
                    movements = expect(client.get(
                        f"/api/v1/invoices/{invoice_id}/movements", headers=headers))["items"]
                    assert first["status"] == "posted" and retry["status"] == "already_posted"
                    assert first["movement_ids"] == retry["movement_ids"] and len(movements) == 2
                    with factory() as session:
                        balances = sorted(balance.available_quantity for balance in
                            session.scalars(select(InventoryBalance).where(
                                InventoryBalance.location_id == location_id)))
                    assert balances == [2, 3]
                    print(json.dumps({"invoice_id": invoice_id, "initial_findings": initial_findings,
                        "original_ocr_quantity": 53, "staff_corrected_quantity": 3,
                        "approved": approval["status"], "first": first["status"],
                        "retry": retry["status"], "receipt_count": len(movements),
                        "inventory_balances": balances,
                        "audit_actions": [item["action"] for item in audit["items"]]},
                        sort_keys=True))
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))
        admin.dispose()


if __name__ == "__main__":
    main()
