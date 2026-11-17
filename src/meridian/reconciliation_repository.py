"""Read-only SQLAlchemy adapter for existing purchasing and invoice tables."""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from meridian.models.invoices import Invoice, InvoiceDocument, InvoiceLine, InvoiceUploadAttempt, Product, Supplier
from meridian.models.inventory import InventoryMovement
from meridian.models.purchasing import PurchaseOrder
from meridian.reconciliation import (
    PriorInvoiceRecord, ProductRecord, PurchaseOrderLineRecord, PurchaseOrderRecord,
    SupplierRecord, normalize_invoice_number,
)


class SqlAlchemyTrustedRepository:
    """Queries only; alias configuration is explicit because schema has no alias table."""

    def __init__(self, session: Session, *, supplier_aliases: dict[str, tuple[str, ...]] | None = None):
        self.session = session
        self.aliases = {code: tuple(names) for code, names in (supplier_aliases or {}).items()}

    def suppliers(self) -> tuple[SupplierRecord, ...]:
        with self.session.no_autoflush:
            rows = self.session.scalars(select(Supplier).order_by(Supplier.id)).all()
        return tuple(SupplierRecord(id=str(row.id), code=row.code, name=row.name,
                                    aliases=self.aliases.get(row.code, ())) for row in rows)

    def products(self) -> tuple[ProductRecord, ...]:
        with self.session.no_autoflush:
            rows = self.session.scalars(select(Product).order_by(Product.id)).all()
        return tuple(ProductRecord(id=str(row.id), name=row.name,
                                   upc=row.upc_normalized, ndc=row.ndc_normalized) for row in rows)

    def purchase_orders(self) -> tuple[PurchaseOrderRecord, ...]:
        with self.session.no_autoflush:
            movements = self.session.scalars(select(InventoryMovement).options(
                selectinload(InventoryMovement.invoice_line).selectinload(InvoiceLine.invoice))).all()
        receipts = {row.id: row for row in movements if row.movement_type == "receipt"
                    and row.purchase_order_line_id is not None}
        received: dict[object, int] = defaultdict(int)
        incomplete: set[object] = set()
        for row in movements:
            if row.movement_type == "receipt" and row.purchase_order_line_id is not None:
                if row.invoice_line is None or row.invoice_line.invoice.status != "posted":
                    incomplete.add(row.purchase_order_line_id)
                else:
                    received[row.purchase_order_line_id] += row.quantity_delta
            elif row.movement_type == "reversal" and row.reverses_movement_id in receipts:
                receipt = receipts[row.reverses_movement_id]
                if row.quantity_delta != -receipt.quantity_delta:
                    incomplete.add(receipt.purchase_order_line_id)
                elif receipt.invoice_line is not None and receipt.invoice_line.invoice.status == "posted":
                    received[receipt.purchase_order_line_id] += row.quantity_delta
        with self.session.no_autoflush:
            orders = self.session.scalars(select(PurchaseOrder).options(
                selectinload(PurchaseOrder.lines)).order_by(PurchaseOrder.id)).all()
        return tuple(PurchaseOrderRecord(
            id=str(order.id), order_number=order.order_number,
            supplier_id=str(order.supplier_id), status=order.status,
            lines=tuple(PurchaseOrderLineRecord(
                id=str(line.id), product_id=str(line.product_id),
                ordered_quantity=line.ordered_quantity,
                expected_unit_cost=line.expected_unit_cost,
                received_quantity=received[line.id],
                receipt_history_complete=line.id not in incomplete)
                for line in sorted(order.lines, key=lambda item: (item.line_number, str(item.id)))))
            for order in orders)

    def prior_invoices(self) -> tuple[PriorInvoiceRecord, ...]:
        with self.session.no_autoflush:
            invoices = self.session.scalars(select(Invoice).order_by(Invoice.id)).all()
            attempts = self.session.execute(select(InvoiceUploadAttempt.id, InvoiceUploadAttempt.invoice_id,
                                                   InvoiceUploadAttempt.identity_status, InvoiceDocument.sha256)
                                            .join(InvoiceDocument, InvoiceUploadAttempt.document_id == InvoiceDocument.id)
                                            ).all()
        hashes: dict[object, set[str]] = defaultdict(set)
        unlinked = []
        for attempt_id, invoice_id, attempt_status, document_hash in attempts:
            if invoice_id is None:
                unlinked.append(PriorInvoiceRecord(id=f"upload:{attempt_id}", supplier_id=None,
                                                   invoice_number_normalized=None,
                                                   source_sha256=document_hash, status=f"upload:{attempt_status}"))
            else:
                hashes[invoice_id].add(document_hash)
        linked = tuple(PriorInvoiceRecord(
            id=str(invoice.id), supplier_id=str(invoice.supplier_id),
            invoice_number_normalized=normalize_invoice_number(invoice.invoice_number_normalized),
            source_sha256=document_hash, status=invoice.status)
            for invoice in invoices for document_hash in (sorted(hashes[invoice.id]) or [None]))
        return linked + tuple(sorted(unlinked, key=lambda item: item.id))
