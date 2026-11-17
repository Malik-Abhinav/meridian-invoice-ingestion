"""Evaluation-only bridge from fixture truth to trusted reference snapshots."""

from datetime import date
from decimal import Decimal

from meridian.evaluation_fixtures import FixtureGroundTruth
from meridian.extraction import (
    CountValue, DateValue, EvidenceRef, ExtractedInvoice, ExtractedLine,
    ExtractionResult, MoneyValue, TextValue,
)
from meridian.ocr import OcrDocument, OcrPage, OcrWord, PreprocessingConfig
from meridian.reconciliation import (
    InMemoryTrustedRepository, ProductRecord, PurchaseOrderLineRecord,
    PurchaseOrderRecord, SupplierRecord,
)


def fixture_repository(truth: FixtureGroundTruth,
                       all_truths: tuple[FixtureGroundTruth, ...]) -> InMemoryTrustedRepository:
    """Only the evaluation harness imports this function; production never sees truth."""
    suppliers = {item.displayed_invoice.supplier.code: SupplierRecord(
        id=item.displayed_invoice.supplier.code, code=item.displayed_invoice.supplier.code,
        name=item.displayed_invoice.supplier.name) for item in all_truths}
    products = []
    po_lines = []
    for line in truth.expected_po_lines:
        identifier = line.product_identifier
        products.append(ProductRecord(id=line.product_sku, name=line.product_sku,
                                      upc=identifier if len(identifier) == 12 else None,
                                      ndc=identifier if len(identifier) == 11 else None))
        po_lines.append(PurchaseOrderLineRecord(id=f"{truth.expected_po_reference}:{line.line_number}",
                                                product_id=line.product_sku,
                                                ordered_quantity=line.quantity,
                                                expected_unit_cost=line.unit_cost,
                                                received_quantity=0,
                                                receipt_history_complete=True))
    po = PurchaseOrderRecord(id=truth.expected_po_reference,
                             order_number=truth.expected_po_reference,
                             supplier_id=truth.expected_po_supplier_code,
                             status="open", lines=tuple(po_lines))
    return InMemoryTrustedRepository(suppliers=tuple(suppliers.values()),
                                     products=tuple(products), purchase_orders=(po,))


def controlled_extraction(truth: FixtureGroundTruth, *,
                          source_sha256: str = "f" * 64) -> tuple[ExtractionResult, OcrDocument]:
    """Accurate invoice-visible values with synthetic high-quality evidence, never OCR output."""
    source_hash = source_sha256
    words: list[OcrWord] = []

    def evidence(value: object) -> tuple[EvidenceRef, ...]:
        index = len(words)
        words.append(OcrWord(text=str(value), confidence=99, left=0, top=index * 10,
                             width=100, height=10, block_number=1,
                             paragraph_number=1, line_number=index + 1, word_number=1))
        return (EvidenceRef(page_number=1, word_indexes=(index,), block_number=1,
                            paragraph_number=1, line_number=index + 1),)

    def text(value: str | None) -> TextValue:
        return TextValue(value=value, evidence=evidence(value) if value is not None else ())

    def count(value: int | None) -> CountValue:
        return CountValue(value=value, evidence=evidence(value) if value is not None else ())

    def money(value: Decimal | None) -> MoneyValue:
        return MoneyValue(value=value, evidence=evidence(value) if value is not None else ())

    visible = truth.displayed_invoice
    products = {product.sku: product for product in visible.products}
    lines = []
    for row, item in enumerate(visible.lines, 1):
        product = products[item.product_sku]
        lines.append(ExtractedLine(source_row_order=row, line_number=count(item.line_number),
                                   description=text(product.description), upc=text(product.upc),
                                   ndc=text(product.ndc), quantity=count(item.quantity),
                                   unit_cost=money(item.unit_cost),
                                   printed_line_total=money(item.line_total)))
    invoice = ExtractedInvoice(
        supplier_name=text(visible.supplier.name), invoice_number=text(visible.invoice_number),
        invoice_date=DateValue(value=visible.invoice_date, evidence=evidence(visible.invoice_date)),
        purchase_order_reference=text(visible.purchase_order_reference), lines=tuple(lines),
        printed_subtotal=money(visible.displayed_totals.subtotal),
        printed_tax=money(visible.displayed_totals.tax),
        printed_total=money(visible.displayed_totals.total))
    ocr = OcrDocument(source_sha256=source_hash, engine_version="controlled-visible-values",
                      configuration=PreprocessingConfig(),
                      pages=(OcrPage(page_number=1, text=" ".join(w.text for w in words),
                                     words=tuple(words), image_width=100, image_height=max(100, len(words) * 10 + 10),
                                     deskew_angle_degrees=0),),
                      normalized_text=" ".join(w.text for w in words))
    extraction = ExtractionResult(source_sha256=source_hash, provider="controlled-visible-values",
                                  model_name="none", repair_attempted=False, invoice=invoice)
    return extraction, ocr
