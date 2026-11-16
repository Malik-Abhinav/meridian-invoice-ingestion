"""Readable, deterministic PDF output from Task 6 input contracts."""

from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfReader

from meridian.pdf_invoices import render_invoice_pdf
from meridian.synthetic import (
    CaseCategory,
    ExpectedGroundTruth,
    InvoiceTotals,
    SupplierLayout,
    SyntheticCase,
    SyntheticInvoiceInput,
    SyntheticLineItem,
    SyntheticProduct,
    SyntheticSupplier,
)


def invoice_for(layout: SupplierLayout, *, count: int = 2, long_name: bool = False,
                totals: bool = True, po: bool = True) -> SyntheticInvoiceInput:
    name = "Imaginary extended-release sample item " + ("with descriptive fictional packaging " * 5)
    product = SyntheticProduct(
        sku="FICT_P1", description=name[:200] if long_name else "Imaginary amber tablets",
        upc="000000000000", ndc="00000000000",
    )
    lines = tuple(
        SyntheticLineItem(line_number=index, product_sku=product.sku,
                          quantity=2, unit_cost=Decimal("1.2345"), line_total=Decimal("2.4690"))
        for index in range(1, count + 1)
    )
    subtotal = Decimal("2.4690") * count
    displayed = InvoiceTotals(subtotal=subtotal, tax=Decimal("0.2000"),
                              total=subtotal + Decimal("0.2000")) if totals else None
    return SyntheticInvoiceInput(
        case_id=f"layout_{layout.value}",
        supplier=SyntheticSupplier(code=f"FICT_{layout.value.upper()}",
                                   name=f"Imaginary {layout.value.title()} Wholesale", layout=layout),
        invoice_number="FICT-INV-001", invoice_date=date(2026, 1, 5),
        purchase_order_reference="FICT-PO-009" if po else None,
        products=(product,), lines=lines, displayed_totals=displayed,
    )


@pytest.mark.parametrize("layout", list(SupplierLayout))
def test_pdf_generation_structure_and_fields(layout: SupplierLayout) -> None:
    invoice = invoice_for(layout)
    pdf = render_invoice_pdf(invoice)
    reader = PdfReader(BytesIO(pdf))
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert pdf.startswith(b"%PDF-")
    assert len(reader.pages) == 1
    for expected in (
        invoice.supplier.name, invoice.invoice_number, "FICT-PO-009", "Imaginary amber tablets",
        "000000000000", "00000000000", "2", "1.2345", "2.4690", "4.9380", "5.1380",
    ):
        assert expected.lower() in text.lower()
    assert len(reader.pages[0].get_contents().get_data()) > 0


@pytest.mark.parametrize("layout", list(SupplierLayout))
def test_optional_po_and_totals_are_not_fabricated(layout: SupplierLayout) -> None:
    invoice = invoice_for(layout, totals=False, po=False)
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(render_invoice_pdf(invoice))).pages)
    assert "FICT-PO-009" not in text
    assert "Subtotal" not in text
    assert "Total" not in text
    assert "FICT-INV-001" in text


@pytest.mark.parametrize("layout", list(SupplierLayout))
def test_pdf_bytes_are_deterministic(layout: SupplierLayout) -> None:
    invoice = invoice_for(layout)
    assert render_invoice_pdf(invoice) == render_invoice_pdf(invoice)


@pytest.mark.parametrize("layout", list(SupplierLayout))
def test_long_product_names_and_many_lines_paginate(layout: SupplierLayout) -> None:
    invoice = invoice_for(layout, count=85, long_name=True)
    reader = PdfReader(BytesIO(render_invoice_pdf(invoice)))
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert len(reader.pages) >= 2
    assert "209.8650" in text  # 85 * 2.4690, not rounded to cents
    assert "210.0650" in text
    assert "Imaginary extended-release" in text
    assert text.count("000000000000") == 85
    assert all(f"Page {index}" in page.extract_text() for index, page in enumerate(reader.pages, 1))


def test_renderer_accepts_generation_input_only() -> None:
    invoice = invoice_for(SupplierLayout.TRADITIONAL)
    truth = ExpectedGroundTruth(
        category=CaseCategory.CLEAN, expected_supplier_code=invoice.supplier.code,
        expected_invoice_number=invoice.invoice_number,
        expected_invoice_date=invoice.invoice_date,
        expected_purchase_order_reference=invoice.purchase_order_reference,
        expected_products=invoice.products, expected_lines=invoice.lines,
        expected_totals=invoice.displayed_totals,
    )
    case = SyntheticCase(generation=invoice, ground_truth=truth)
    assert render_invoice_pdf(case.generation).startswith(b"%PDF-")
    with pytest.raises(TypeError):
        render_invoice_pdf(case)  # type: ignore[arg-type]
