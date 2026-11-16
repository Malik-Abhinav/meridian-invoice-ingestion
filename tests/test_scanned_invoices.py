"""Image-only scan behavior and isolation from evaluation ground truth."""

from datetime import date
from decimal import Decimal
from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image, ImageChops, ImageStat
from pypdf import PdfReader
from pydantic import ValidationError

from meridian.pdf_invoices import render_invoice_pdf
from meridian.scanned_invoices import (
    ScanParameters,
    ScanProfile,
    degrade_image,
    rasterize_pdf,
    scan_invoice,
)
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
    derive_case_seed,
)


def invoice_for(layout: SupplierLayout = SupplierLayout.TRADITIONAL, *, count: int = 2) -> SyntheticInvoiceInput:
    product = SyntheticProduct(
        sku="FICT_PRODUCT", description="Imaginary extended capsule package with fictional details",
        upc="000000000000",
    )
    lines = tuple(
        SyntheticLineItem(line_number=index, product_sku=product.sku, quantity=2,
                          unit_cost=Decimal("1.2345"), line_total=Decimal("2.4690"))
        for index in range(1, count + 1)
    )
    subtotal = Decimal("2.4690") * count
    return SyntheticInvoiceInput(
        case_id=f"scan_{layout.value}_{count}",
        supplier=SyntheticSupplier(code="FICT_SUPPLIER", name="Imaginary Wholesaler", layout=layout),
        invoice_number="FICT-INV-001", invoice_date=date(2026, 1, 5),
        purchase_order_reference="FICT-PO-009", products=(product,), lines=lines,
        displayed_totals=InvoiceTotals(subtotal=subtotal, tax=Decimal("0.2000"),
                                       total=subtotal + Decimal("0.2000")),
    )


@pytest.mark.parametrize("profile", list(ScanProfile))
def test_all_profiles_produce_image_only_pdf(profile: ScanProfile) -> None:
    invoice = invoice_for()
    source = render_invoice_pdf(invoice)
    result = scan_invoice(source, invoice, master_seed=42, profile=profile, dpi=96)
    reader = PdfReader(BytesIO(result.pdf_bytes))
    assert len(reader.pages) == 1
    assert reader.pages[0].extract_text().strip() == ""
    assert "/XObject" in reader.pages[0]["/Resources"]
    assert b"FICT-INV-001" not in result.pdf_bytes
    assert "FICT-INV-001" in PdfReader(BytesIO(source)).pages[0].extract_text()
    assert source == render_invoice_pdf(invoice)


@pytest.mark.parametrize("profile", list(ScanProfile))
def test_scan_bytes_images_and_metadata_are_deterministic(profile: ScanProfile) -> None:
    invoice = invoice_for()
    source = render_invoice_pdf(invoice)
    first = scan_invoice(source, invoice, master_seed=917, profile=profile, dpi=96)
    second = scan_invoice(source, invoice, master_seed=917, profile=profile, dpi=96)
    assert first.pdf_bytes == second.pdf_bytes
    assert first.metadata == second.metadata
    assert rasterize_pdf(first.pdf_bytes, dpi=72)[0].tobytes() == rasterize_pdf(
        second.pdf_bytes, dpi=72
    )[0].tobytes()


@pytest.mark.parametrize("layout", list(SupplierLayout))
def test_metadata_identifies_source_template_and_parameters(layout: SupplierLayout) -> None:
    invoice = invoice_for(layout)
    source = render_invoice_pdf(invoice)
    result = scan_invoice(source, invoice, master_seed=17, profile=ScanProfile.COMBINED, dpi=96)
    metadata = result.metadata
    page = metadata.pages[0]
    assert metadata.source_case_id == invoice.case_id
    assert metadata.source_invoice_number == invoice.invoice_number
    assert metadata.source_pdf_sha256 == sha256(source).hexdigest()
    assert metadata.scanned_pdf_sha256 == sha256(result.pdf_bytes).hexdigest()
    assert metadata.supplier_code == invoice.supplier.code
    assert metadata.supplier_template == layout
    assert metadata.degradation_type == ScanProfile.COMBINED
    assert metadata.random_seed == derive_case_seed(17, invoice.case_id)
    assert metadata.output_format == "pdf" and metadata.page_count == 1
    assert page.page_number == 1 and page.resolution_dpi == 96
    assert page.width_px >= page.source_width_px
    assert page.height_px >= page.source_height_px
    assert page.parameters.blur_radius == 1.0
    assert page.parameters.contrast_factor == 0.78
    assert page.parameters.jpeg_quality == 55
    assert 0.3 <= abs(page.parameters.rotation_degrees) <= 0.8


def test_blur_contrast_rotation_and_jpeg_transform_pixels_without_mutation() -> None:
    original = Image.new("RGB", (120, 80), "white")
    for x in range(35, 85):
        for y in range(20, 60):
            original.putpixel((x, y), (0, 0, 0))
    original_bytes = original.tobytes()
    mild = degrade_image(original, ScanParameters(blur_radius=0.8))
    moderate = degrade_image(original, ScanParameters(blur_radius=1.5))
    contrast = degrade_image(original, ScanParameters(contrast_factor=0.72))
    rotation = degrade_image(original, ScanParameters(rotation_degrees=1.0))
    jpeg = degrade_image(original, ScanParameters(jpeg_quality=48))
    assert ImageChops.difference(original, mild).getbbox() is not None
    assert ImageChops.difference(mild, moderate).getbbox() is not None
    assert ImageStat.Stat(contrast.convert("L")).stddev[0] < ImageStat.Stat(original.convert("L")).stddev[0]
    assert rotation.width > original.width and rotation.height > original.height
    assert ImageChops.difference(original, jpeg).getbbox() is not None
    assert jpeg.tobytes() == degrade_image(original, ScanParameters(jpeg_quality=48)).tobytes()
    assert original.tobytes() == original_bytes


def test_multi_page_scan_keeps_page_count_order_and_independent_seeds() -> None:
    invoice = invoice_for(SupplierLayout.PURCHASE_ORDER, count=55)
    source = render_invoice_pdf(invoice)
    source_pages = PdfReader(BytesIO(source)).pages
    assert len(source_pages) >= 2
    result = scan_invoice(source, invoice, master_seed=42, profile=ScanProfile.ROTATION, dpi=96)
    scanned_pages = PdfReader(BytesIO(result.pdf_bytes)).pages
    assert len(scanned_pages) == result.metadata.page_count == len(source_pages)
    assert all(page.extract_text().strip() == "" for page in scanned_pages)
    assert [page.page_number for page in result.metadata.pages] == list(range(1, len(scanned_pages) + 1))
    assert len({page.page_seed for page in result.metadata.pages}) == len(scanned_pages)
    assert all(page.width_px >= page.source_width_px for page in result.metadata.pages)
    assert all(page.height_px >= page.source_height_px for page in result.metadata.pages)


def test_ground_truth_is_not_scan_input_and_remains_unchanged() -> None:
    invoice = invoice_for()
    truth = ExpectedGroundTruth(
        category=CaseCategory.CLEAN, expected_supplier_code=invoice.supplier.code,
        expected_invoice_number=invoice.invoice_number, expected_invoice_date=invoice.invoice_date,
        expected_purchase_order_reference=invoice.purchase_order_reference,
        expected_products=invoice.products, expected_lines=invoice.lines,
        expected_totals=invoice.displayed_totals,
    )
    case = SyntheticCase(generation=invoice, ground_truth=truth)
    before = case.ground_truth.model_dump()
    source = render_invoice_pdf(case.generation)
    scan_invoice(source, case.generation, master_seed=42, profile=ScanProfile.MILD_BLUR, dpi=96)
    assert case.ground_truth.model_dump() == before
    assert source == render_invoice_pdf(case.generation)
    with pytest.raises(TypeError):
        scan_invoice(source, case, master_seed=42, profile=ScanProfile.CLEAN, dpi=96)  # type: ignore[arg-type]


def test_rejects_invalid_resolution_and_profile() -> None:
    invoice = invoice_for()
    source = render_invoice_pdf(invoice)
    for resolution in (0, 71, 301, True, 96.5):
        with pytest.raises(ValueError):
            scan_invoice(source, invoice, master_seed=42, profile=ScanProfile.CLEAN,
                         dpi=resolution)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        scan_invoice(source, invoice, master_seed=42, profile="clean_raster")  # type: ignore[arg-type]
    other_invoice = invoice_for(SupplierLayout.COMPACT)
    with pytest.raises(ValueError, match="does not match"):
        scan_invoice(source, other_invoice, master_seed=42, profile=ScanProfile.CLEAN, dpi=96)
    for parameters in ({"blur_radius": -0.1}, {"contrast_factor": 0.0},
                       {"rotation_degrees": 6.0}, {"jpeg_quality": 0}):
        with pytest.raises(ValidationError):
            ScanParameters(**parameters)
