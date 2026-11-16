"""Write three fictional, review-only invoice PDFs; no dataset generation."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from meridian.pdf_invoices import render_invoice_pdf
from meridian.synthetic import (
    InvoiceTotals,
    SupplierLayout,
    SyntheticInvoiceInput,
    SyntheticLineItem,
    SyntheticProduct,
    SyntheticSupplier,
)


OUTPUT = Path(__file__).resolve().parents[1] / "output" / "pdf"
SUPPLIERS = (
    ("supplier_a_traditional.pdf", "FICT_A", "Imaginary Harbor Wholesale", SupplierLayout.TRADITIONAL),
    ("supplier_b_compact.pdf", "FICT_B", "Fable Ridge Distribution", SupplierLayout.COMPACT),
    ("supplier_c_purchase_order.pdf", "FICT_C", "Northstar Fictional Supply", SupplierLayout.PURCHASE_ORDER),
)


def sample_invoice(code: str, name: str, layout: SupplierLayout) -> SyntheticInvoiceInput:
    products = (
        SyntheticProduct(sku="FICT_AMBER", description="Imaginary amber tablets, 30-count display carton",
                         upc="000000000000"),
        SyntheticProduct(sku="FICT_BLUE", description="Fictional blue capsule pack with extended descriptive label",
                         ndc="00000000000"),
    )
    lines = (
        SyntheticLineItem(line_number=1, product_sku=products[0].sku, quantity=4,
                          unit_cost=Decimal("8.1250"), line_total=Decimal("32.5000")),
        SyntheticLineItem(line_number=2, product_sku=products[1].sku, quantity=3,
                          unit_cost=Decimal("14.3750"), line_total=Decimal("43.1250")),
        SyntheticLineItem(line_number=3, product_sku=products[0].sku, quantity=2,
                          unit_cost=Decimal("8.1250"), line_total=Decimal("16.2500")),
        SyntheticLineItem(line_number=4, product_sku=products[1].sku, quantity=1,
                          unit_cost=Decimal("14.3750"), line_total=Decimal("14.3750")),
    )
    subtotal = sum((line.line_total for line in lines), Decimal("0"))
    return SyntheticInvoiceInput(
        case_id=f"review_{code.lower()}", supplier=SyntheticSupplier(code=code, name=name, layout=layout),
        invoice_number=f"{code}-INV-0001", invoice_date=date(2026, 1, 5),
        purchase_order_reference=f"{code}-PO-0042", products=products, lines=lines,
        displayed_totals=InvoiceTotals(subtotal=subtotal, tax=Decimal("1.2500"),
                                       total=subtotal + Decimal("1.2500")),
    )


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for filename, code, name, layout in SUPPLIERS:
        (OUTPUT / filename).write_bytes(render_invoice_pdf(sample_invoice(code, name, layout)))


if __name__ == "__main__":
    main()
