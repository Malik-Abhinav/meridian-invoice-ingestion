"""Deterministic image-only scan variants of existing invoice PDFs."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from io import BytesIO
from typing import Literal

import pypdfium2 as pdfium
from PIL import Image, ImageEnhance, ImageFilter
from pydantic import BaseModel, ConfigDict, Field
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from meridian.pdf_invoices import render_invoice_pdf
from meridian.synthetic import SupplierLayout, SyntheticInvoiceInput, derive_case_seed


class ScanProfile(StrEnum):
    CLEAN = "clean_raster"
    MILD_BLUR = "mild_blur"
    MODERATE_BLUR = "moderate_blur"
    ROTATION = "rotation"
    LOW_CONTRAST = "low_contrast"
    JPEG_ARTIFACTS = "jpeg_artifacts"
    COMBINED = "combined"


class ScanParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    blur_radius: float = Field(default=0.0, ge=0, le=5)
    rotation_degrees: float = Field(default=0.0, ge=-5, le=5)
    contrast_factor: float = Field(default=1.0, gt=0, le=2)
    jpeg_quality: int | None = Field(default=None, ge=1, le=95)


class ScanPageMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    page_number: int
    page_seed: int
    source_width_px: int
    source_height_px: int
    width_px: int
    height_px: int
    resolution_dpi: int
    parameters: ScanParameters


class ScanMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_case_id: str
    source_invoice_number: str
    source_pdf_sha256: str
    supplier_code: str
    supplier_template: SupplierLayout
    degradation_type: ScanProfile
    random_seed: int
    output_format: Literal["pdf"] = "pdf"
    scanned_pdf_sha256: str
    page_count: int
    pages: tuple[ScanPageMetadata, ...]


@dataclass(frozen=True)
class ScannedInvoice:
    pdf_bytes: bytes
    metadata: ScanMetadata


def rasterize_pdf(source_pdf: bytes, *, dpi: int = 150) -> tuple[Image.Image, ...]:
    """Rasterize every PDF page to a detached RGB image."""
    if not isinstance(source_pdf, bytes) or not source_pdf.startswith(b"%PDF-"):
        raise ValueError("source_pdf must be PDF bytes")
    if type(dpi) is not int or not 72 <= dpi <= 300:
        raise ValueError("dpi must be an integer from 72 to 300")
    images = []
    with pdfium.PdfDocument(source_pdf) as document:
        for index in range(len(document)):
            page = document[index]
            try:
                bitmap = page.render(scale=dpi / 72)
                try:
                    images.append(bitmap.to_pil().convert("RGB").copy())
                finally:
                    bitmap.close()
            finally:
                page.close()
    if not images:
        raise ValueError("source PDF has no pages")
    return tuple(images)


def _page_seed(case_seed: int, page_number: int) -> int:
    payload = f"meridian-scan-page-v1\0{case_seed}\0{page_number}".encode("ascii")
    return int.from_bytes(sha256(payload).digest()[:8], "big")


def _parameters(profile: ScanProfile, page_seed: int) -> ScanParameters:
    if profile == ScanProfile.CLEAN:
        return ScanParameters()
    if profile == ScanProfile.MILD_BLUR:
        return ScanParameters(blur_radius=0.8)
    if profile == ScanProfile.MODERATE_BLUR:
        return ScanParameters(blur_radius=1.5)
    if profile == ScanProfile.ROTATION:
        angle = round(0.5 + (page_seed % 701) / 1000, 3)
        return ScanParameters(rotation_degrees=-angle if page_seed & 1 else angle)
    if profile == ScanProfile.LOW_CONTRAST:
        return ScanParameters(contrast_factor=0.72)
    if profile == ScanProfile.JPEG_ARTIFACTS:
        return ScanParameters(jpeg_quality=48)
    if profile == ScanProfile.COMBINED:
        angle = round(0.3 + (page_seed % 501) / 1000, 3)
        return ScanParameters(
            blur_radius=1.0,
            rotation_degrees=-angle if page_seed & 1 else angle,
            contrast_factor=0.78,
            jpeg_quality=55,
        )
    raise ValueError(f"unsupported scan profile: {profile!r}")


def degrade_image(image: Image.Image, parameters: ScanParameters) -> Image.Image:
    """Apply a resolved profile without changing the source image."""
    result = image.convert("RGB")
    if parameters.blur_radius:
        result = result.filter(ImageFilter.GaussianBlur(parameters.blur_radius))
    if parameters.contrast_factor != 1.0:
        result = ImageEnhance.Contrast(result).enhance(parameters.contrast_factor)
    if parameters.rotation_degrees:
        result = result.rotate(
            parameters.rotation_degrees,
            resample=Image.Resampling.BICUBIC,
            expand=True,
            fillcolor=(255, 255, 255),
        )
    if parameters.jpeg_quality is not None:
        buffer = BytesIO()
        result.save(buffer, format="JPEG", quality=parameters.jpeg_quality, subsampling=2,
                    optimize=False, progressive=False)
        with Image.open(BytesIO(buffer.getvalue())) as compressed:
            result = compressed.convert("RGB")
    return result


def _image_pdf(images: tuple[Image.Image, ...], dpi: int) -> bytes:
    stream = BytesIO()
    output = canvas.Canvas(stream, invariant=1, pageCompression=1)
    for image in images:
        width_pt = image.width * 72 / dpi
        height_pt = image.height * 72 / dpi
        output.setPageSize((width_pt, height_pt))
        output.drawImage(ImageReader(image), 0, 0, width=width_pt, height=height_pt)
        output.showPage()
    output.save()
    return stream.getvalue()


def scan_invoice(source_pdf: bytes, invoice: SyntheticInvoiceInput, *, master_seed: int,
                 profile: ScanProfile, dpi: int = 150) -> ScannedInvoice:
    """Create an image-only PDF and separate metadata from a clean invoice PDF."""
    if not isinstance(invoice, SyntheticInvoiceInput):
        raise TypeError("scan_invoice accepts SyntheticInvoiceInput only")
    if not isinstance(profile, ScanProfile):
        raise TypeError("profile must be a ScanProfile")
    if source_pdf != render_invoice_pdf(invoice):
        raise ValueError("source PDF does not match the supplied invoice")
    case_seed = derive_case_seed(master_seed, invoice.case_id)
    source_images = rasterize_pdf(source_pdf, dpi=dpi)
    scanned_images = []
    pages = []
    for number, image in enumerate(source_images, 1):
        page_seed = _page_seed(case_seed, number)
        parameters = _parameters(profile, page_seed)
        scanned = degrade_image(image, parameters)
        scanned_images.append(scanned)
        pages.append(ScanPageMetadata(
            page_number=number, page_seed=page_seed,
            source_width_px=image.width, source_height_px=image.height,
            width_px=scanned.width, height_px=scanned.height,
            resolution_dpi=dpi, parameters=parameters,
        ))
    pdf_bytes = _image_pdf(tuple(scanned_images), dpi)
    metadata = ScanMetadata(
        source_case_id=invoice.case_id,
        source_invoice_number=invoice.invoice_number,
        source_pdf_sha256=sha256(source_pdf).hexdigest(),
        supplier_code=invoice.supplier.code,
        supplier_template=invoice.supplier.layout,
        degradation_type=profile,
        random_seed=case_seed,
        scanned_pdf_sha256=sha256(pdf_bytes).hexdigest(),
        page_count=len(pages), pages=tuple(pages),
    )
    return ScannedInvoice(pdf_bytes=pdf_bytes, metadata=metadata)
