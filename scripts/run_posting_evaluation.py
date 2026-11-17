"""Controlled posting checks in a disposable PostgreSQL database.

This evaluation-only harness reuses the test seeder; production posting never reads
fixture ground truth or imports test code.
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from meridian.inventory_posting import PostingConflict, PostingStatus, post_invoice
from meridian.models import PurchaseOrder, PurchaseOrderLine


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_inventory_posting import seed, state  # noqa: E402 - evaluation-only fixtures


def main() -> None:
    source = os.environ.get("DATABASE_URL")
    if not source or make_url(source).get_backend_name() != "postgresql":
        raise RuntimeError("A local PostgreSQL DATABASE_URL is required")
    parsed = make_url(source)
    name = f"meridian_post_eval_{uuid4().hex[:16]}"
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
            clean = seed(factory)
            first = post_invoice(factory, clean.request("clean"))
            again = post_invoice(factory, clean.request("clean"))
            print(json.dumps({"case": "clean_once", "first": first.status.value,
                              "retry": again.status.value,
                              "receipt_count": len(state(factory, clean.invoice_id)[1])}))

            parallel = seed(factory, reuse=clean, number="FICT_A-INV-PARALLEL",
                            source_hash="a" * 64, new_po_reference="FICT_A-PO-PARALLEL")
            gate = Barrier(2)
            def worker():
                gate.wait()
                return post_invoice(factory, parallel.request("parallel")).status.value
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: worker(), range(2)))
            assert set(outcomes) == {PostingStatus.POSTED.value,
                                     PostingStatus.ALREADY_POSTED.value}
            print(json.dumps({"case": "concurrent_duplicate", "outcomes": sorted(outcomes),
                              "receipt_count": len(state(factory, parallel.invoice_id)[1])}))

            race_first = seed(factory, reuse=clean, number="FICT_A-INV-RACE-1",
                              source_hash="e" * 64, new_po_reference="FICT_A-PO-RACE")
            race_second = seed(factory, reuse=race_first, number="FICT_A-INV-RACE-2",
                               source_hash="0" * 64,
                               existing_po_reference="FICT_A-PO-RACE")
            race_gate = Barrier(2)
            def compete(item, key):
                race_gate.wait()
                try:
                    return post_invoice(factory, item.request(key)).status.value
                except PostingConflict as exc:
                    return exc.code.value
            with ThreadPoolExecutor(max_workers=2) as pool:
                race_outcomes = list(pool.map(lambda pair: compete(*pair),
                                              ((race_first, "race-one"),
                                               (race_second, "race-two"))))
            assert set(race_outcomes) == {"posted", "insufficient_po_quantity"}
            assert sum(len(state(factory, item.invoice_id)[1])
                       for item in (race_first, race_second)) == 2
            print(json.dumps({"case": "concurrent_po_limit",
                              "outcomes": sorted(race_outcomes), "receipt_count": 2}))

            first_po = seed(factory, reuse=clean, number="FICT_A-INV-LIMIT-1",
                            source_hash="b" * 64, new_po_reference="FICT_A-PO-LIMIT")
            competitor = seed(factory, reuse=first_po, number="FICT_A-INV-LIMIT-2",
                              source_hash="c" * 64,
                              existing_po_reference="FICT_A-PO-LIMIT")
            post_invoice(factory, first_po.request("limit-first"))
            before = state(factory, competitor.invoice_id)
            try:
                post_invoice(factory, competitor.request("limit-second"))
                raise AssertionError("Over-receipt unexpectedly posted")
            except PostingConflict as exc:
                over_code = exc.code.value
            after = state(factory, competitor.invoice_id)
            assert before == after
            print(json.dumps({"case": "over_receipt", "error": over_code,
                              "unchanged": before == after}))

            mismatch = seed(factory, reuse=clean, number="FICT_A-INV-MISMATCH",
                            source_hash="d" * 64, new_po_reference="FICT_A-PO-MISMATCH")
            with factory.begin() as session:
                po_line = session.get(PurchaseOrderLine, mismatch.po_line_ids[0])
                session.get(PurchaseOrder, po_line.purchase_order_id).status = "closed"
            before = state(factory, mismatch.invoice_id)
            try:
                post_invoice(factory, mismatch.request("mismatch"))
                raise AssertionError("Closed PO unexpectedly posted")
            except PostingConflict as exc:
                mismatch_code = exc.code.value
            after = state(factory, mismatch.invoice_id)
            assert before == after
            print(json.dumps({"case": "po_mismatch", "error": mismatch_code,
                              "unchanged": before == after}))
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))
        admin.dispose()


if __name__ == "__main__":
    main()
