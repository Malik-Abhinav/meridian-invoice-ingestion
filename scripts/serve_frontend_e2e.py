"""Run a real FastAPI browser fixture on disposable PostgreSQL and PDF storage."""

import os
import secrets
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from meridian.api.app import create_app
from meridian.api.config import ApiSettings
from meridian.models import Location, Product, PurchaseOrder, PurchaseOrderLine, Supplier

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_api import TRUTH  # noqa: E402 - fictional fixture inputs only


def main() -> None:
    source = os.environ.get("DATABASE_URL")
    if not source or make_url(source).get_backend_name() != "postgresql":
        raise RuntimeError("A disposable PostgreSQL-capable DATABASE_URL is required")
    parsed = make_url(source)
    name = f"meridian_frontend_e2e_{uuid4().hex[:16]}"
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
            token = secrets.token_urlsafe(32)
            with TemporaryDirectory(prefix="meridian-browser-e2e-") as storage:
                token_file = Path(os.environ.get("E2E_TOKEN_FILE", str(Path(storage) / "staff-token")))
                descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "w") as output:
                    output.write(token)
                app = create_app(ApiSettings(storage_root=Path(storage), dev_auth_enabled=True,
                    dev_staff_token=token), factory)
                print(f"E2E_READY token_file={token_file} location_id={location_id}", flush=True)
                try:
                    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
                finally:
                    if token_file.parent == Path(storage):
                        token_file.unlink(missing_ok=True)
                    else:
                        # A bind-mounted host file cannot be unlinked from inside Docker.
                        token_file.write_text("")
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))
        admin.dispose()


if __name__ == "__main__":
    main()
