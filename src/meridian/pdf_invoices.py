"""Selectable-text invoice PDFs for three fictional supplier layouts."""

from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from meridian.synthetic import SupplierLayout, SyntheticInvoiceInput, SyntheticLineItem, SyntheticProduct


INK = colors.HexColor("#183047")
MUTED = colors.HexColor("#556778")
PALE = colors.HexColor("#edf2f5")
ACCENT = colors.HexColor("#146b78")
PAGE_WIDTH, PAGE_HEIGHT = A4
CONTENT_WIDTH = PAGE_WIDTH - 84


def _style(name: str, size: float = 9, *, bold: bool = False, right: bool = False,
           color=INK, leading: float | None = None) -> ParagraphStyle:
    return ParagraphStyle(
        name,
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size,
        leading=leading or size * 1.35,
        textColor=color,
        alignment=TA_RIGHT if right else TA_LEFT,
        splitLongWords=1,
    )


BODY = _style("body")
SMALL = _style("small", 7.5, color=MUTED)
SMALL_RIGHT = _style("small_right", 8, right=True)
NUMBER = _style("number", 9, right=True)
HEADING = _style("heading", 18, bold=True)
SECTION = _style("section", 10, bold=True, color=ACCENT)
COMPACT_BODY = _style("compact_body", 8, leading=10)
COMPACT_NUMBER = _style("compact_number", 8, right=True, leading=10)


def _p(text: str, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(escape(text), style)


def _money(value) -> str:
    # Four places preserve the NUMERIC(18,4) value without implicit currency rounding.
    return f"{value:.4f}"


def _identifier(product: SyntheticProduct) -> str:
    parts = []
    if product.upc is not None:
        parts.append(f"UPC {product.upc}")
    if product.ndc is not None:
        parts.append(f"NDC {product.ndc}")
    return "  |  ".join(parts)


def _product_cell(product: SyntheticProduct, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(
        f"{escape(product.description)}<br/><font color='#556778' size='7'>{escape(_identifier(product))}</font>",
        style,
    )


def _table(headers: list[str], rows: list[list[Paragraph]], widths: list[float], *,
           compact: bool = False, grid: bool = False) -> Table:
    header_style = _style("header_compact" if compact else "header", 7.5, bold=True, color=colors.white)
    data = [[_p(label, header_style) for label in headers], *rows]
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    padding = 5 if compact else 7
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT if compact else INK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), padding),
        ("RIGHTPADDING", (0, 0), (-1, -1), padding),
        ("TOPPADDING", (0, 0), (-1, -1), padding),
        ("BOTTOMPADDING", (0, 0), (-1, -1), padding),
    ]
    if grid:
        commands.append(("LINEBELOW", (0, 0), (-1, -1), 0.35, colors.HexColor("#bdcbd3")))
    table.setStyle(TableStyle(commands))
    return table


def _totals(invoice: SyntheticInvoiceInput, *, compact: bool = False) -> Table | None:
    totals = invoice.displayed_totals
    if totals is None:
        return None
    label_style = COMPACT_BODY if compact else BODY
    amount_style = COMPACT_NUMBER if compact else NUMBER
    data = [
        [_p("Subtotal", label_style), _p(_money(totals.subtotal), amount_style)],
        [_p("Tax", label_style), _p(_money(totals.tax), amount_style)],
        [_p("Total", _style("total_label", 10, bold=True)),
         _p(_money(totals.total), _style("total_value", 10, bold=True, right=True))],
    ]
    table = Table(data, colWidths=[95, 95], hAlign="RIGHT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, -1), (-1, -1), PALE),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _traditional(invoice: SyntheticInvoiceInput) -> list:
    products = {product.sku: product for product in invoice.products}
    story = [
        _p(invoice.supplier.name, HEADING),
        Spacer(1, 9),
        _p("INVOICE", SECTION),
        Spacer(1, 13),
    ]
    metadata = [
        [_p("Invoice number", SMALL), _p(invoice.invoice_number)],
        [_p("Invoice date", SMALL), _p(invoice.invoice_date.isoformat())],
    ]
    if invoice.purchase_order_reference is not None:
        metadata.append([_p("Purchase order", SMALL), _p(invoice.purchase_order_reference)])
    meta_table = Table(metadata, colWidths=[115, CONTENT_WIDTH - 115], hAlign="LEFT")
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.extend([meta_table, Spacer(1, 18)])
    rows = [
        [_product_cell(products[line.product_sku]), _p(str(line.quantity), NUMBER),
         _p(_money(line.unit_cost), NUMBER), _p(_money(line.line_total), NUMBER)]
        for line in invoice.lines
    ]
    story.append(_table(["Product / identifier", "Qty", "Unit cost", "Line total"], rows,
                        [CONTENT_WIDTH - 230, 45, 90, 95]))
    totals = _totals(invoice)
    if totals is not None:
        story.extend([Spacer(1, 18), KeepTogether([totals])])
    return story


def _compact(invoice: SyntheticInvoiceInput) -> list:
    products = {product.sku: product for product in invoice.products}
    panel = [
        _p("INVOICE " + invoice.invoice_number, _style("compact_panel_title", 10, bold=True, color=colors.white)),
        _p(invoice.invoice_date.strftime("%d %b %Y"), _style("compact_panel_date", 8, color=colors.white)),
    ]
    if invoice.purchase_order_reference is not None:
        panel.append(_p("PO " + invoice.purchase_order_reference,
                        _style("compact_panel_po", 8, color=colors.white)))
    header = Table([[
        _p(invoice.supplier.name, _style("compact_supplier", 14, bold=True)),
        panel,
    ]], colWidths=[CONTENT_WIDTH - 205, 205])
    header.setStyle(TableStyle([
        ("BACKGROUND", (1, 0), (1, 0), INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story = [header, Spacer(1, 13)]
    totals = _totals(invoice, compact=True)
    if totals is not None:
        story.extend([totals, Spacer(1, 13)])
    rows = [
        [_p(str(line.quantity), COMPACT_NUMBER), _product_cell(products[line.product_sku], COMPACT_BODY),
         _p(_money(line.line_total), COMPACT_NUMBER), _p(_money(line.unit_cost), COMPACT_NUMBER)]
        for line in invoice.lines
    ]
    story.append(_table(["Qty", "Description / code", "Extended", "Unit"], rows,
                        [40, CONTENT_WIDTH - 215, 90, 85], compact=True))
    return story


def _purchase_order(invoice: SyntheticInvoiceInput) -> list:
    products = {product.sku: product for product in invoice.products}
    story = [
        _p(invoice.supplier.name.upper(), _style("po_supplier", 11, bold=True, color=ACCENT)),
        Spacer(1, 9),
    ]
    if invoice.purchase_order_reference is not None:
        story.extend([_p("PURCHASE ORDER " + invoice.purchase_order_reference, HEADING), Spacer(1, 8)])
    story.extend([
        _p("SUPPLIER INVOICE  " + invoice.invoice_number, SECTION),
        _p("Issued " + invoice.invoice_date.strftime("%B %d, %Y"), SMALL),
        Spacer(1, 17),
    ])
    groups: list[tuple[str, list[SyntheticLineItem]]] = [
        ("NDC-coded products", []), ("UPC-coded products", []),
    ]
    for line in invoice.lines:
        product = products[line.product_sku]
        groups[0 if product.ndc is not None else 1][1].append(line)
    for title, lines in groups:
        if not lines:
            continue
        story.extend([_p(title, SECTION), Spacer(1, 5)])
        rows = [
            [_p(str(line.line_number), SMALL), _product_cell(products[line.product_sku]),
             _p(_money(line.unit_cost), NUMBER), _p(str(line.quantity), NUMBER),
             _p(_money(line.line_total), NUMBER)]
            for line in lines
        ]
        story.extend([
            _table(["#", "Product / identifier", "Cost per unit", "Units", "Extended"], rows,
                   [28, CONTENT_WIDTH - 258, 92, 45, 93], grid=True),
            Spacer(1, 15),
        ])
    totals = _totals(invoice)
    if totals is not None:
        story.extend([_p("Invoice summary", SECTION), Spacer(1, 5), KeepTogether([totals])])
    return story


_LAYOUTS = {
    SupplierLayout.TRADITIONAL: _traditional,
    SupplierLayout.COMPACT: _compact,
    SupplierLayout.PURCHASE_ORDER: _purchase_order,
}


def _page_footer(pdf_canvas: canvas.Canvas, document: SimpleDocTemplate) -> None:
    pdf_canvas.saveState()
    pdf_canvas.setStrokeColor(colors.HexColor("#bdcbd3"))
    pdf_canvas.line(42, 35, PAGE_WIDTH - 42, 35)
    pdf_canvas.setFont("Helvetica", 7)
    pdf_canvas.setFillColor(MUTED)
    pdf_canvas.drawString(42, 23, "Fictional supplier invoice")
    pdf_canvas.drawRightString(PAGE_WIDTH - 42, 23, f"Page {document.page}")
    pdf_canvas.restoreState()


def render_invoice_pdf(invoice: SyntheticInvoiceInput) -> bytes:
    """Render one validated input as a deterministic, selectable-text PDF."""
    if not isinstance(invoice, SyntheticInvoiceInput):
        raise TypeError("render_invoice_pdf accepts SyntheticInvoiceInput only")
    stream = BytesIO()
    document = SimpleDocTemplate(
        stream, pagesize=A4, leftMargin=42, rightMargin=42, topMargin=45, bottomMargin=52,
        title=f"Invoice {invoice.invoice_number}", author=invoice.supplier.name,
        invariant=1,
    )
    document.build(_LAYOUTS[invoice.supplier.layout](invoice), onFirstPage=_page_footer,
                   onLaterPages=_page_footer)
    return stream.getvalue()
