"""Versioned, evaluation-only synthetic invoice corpus; never used by posting."""

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import random
from typing import Literal

from pydantic import Field, model_validator

from meridian.pdf_invoices import render_invoice_pdf
from meridian.reconciliation import (
    InMemoryTrustedRepository, PriorInvoiceRecord, ProductRecord,
    PurchaseOrderLineRecord, PurchaseOrderRecord, SupplierRecord,
)
from meridian.scanned_invoices import ScanMetadata, ScanParameters, ScanProfile, scan_invoice
from meridian.synthetic import (
    CaseCategory, ExpectedGroundTruth, InvoiceTotals, SupplierLayout,
    SyntheticCase, SyntheticInvoiceInput, SyntheticLineItem, SyntheticModel,
    SyntheticProduct, SyntheticSupplier, derive_case_seed,
)

VERSION = "1"
GENERATOR_VERSION = "task17-v1"
MASTER_SEED = 2029
CATEGORIES = tuple(CaseCategory)
SUPPLIERS = (
    SyntheticSupplier(code="FICT_A", name="Imaginary Harbor Wholesale", layout=SupplierLayout.TRADITIONAL),
    SyntheticSupplier(code="FICT_B", name="Fable Ridge Distribution", layout=SupplierLayout.COMPACT),
    SyntheticSupplier(code="FICT_C", name="Northstar Fictional Supply", layout=SupplierLayout.PURCHASE_ORDER),
)
DESCRIPTIONS = (
    "Fictional sterile sample tray", "Imaginary gauze demonstration pack",
    "Fable blue exam sleeve", "Mock clinical swab carton",
    "Fictional absorbent pad case", "Imaginary specimen tube bundle",
    "Fable practice dressing kit", "Mock disposable drape packet",
    "Fictional amber storage vial", "Imaginary nitrile sample box",
)


class CorpusTruth(SyntheticModel):
    """Evaluation-only business labels and trusted data, never sent to a renderer."""

    case: SyntheticCase
    trusted_supplier: SupplierRecord
    trusted_products: tuple[ProductRecord, ...]
    trusted_po: PurchaseOrderRecord
    prior_invoices: tuple[PriorInvoiceRecord, ...] = ()
    expected_validation_codes: tuple[str, ...] = ()
    expected_reconciliation_codes: tuple[str, ...] = ()
    duplicate_relationship: Literal["distinct_bytes_same_identity"] | None = None

    def repository(self) -> InMemoryTrustedRepository:
        return InMemoryTrustedRepository(
            suppliers=(self.trusted_supplier,), products=self.trusted_products,
            purchase_orders=(self.trusted_po,), prior_invoices=self.prior_invoices,
        )


class CorpusEntry(SyntheticModel):
    case_id: str
    category: CaseCategory
    supplier_code: str
    layout: SupplierLayout
    related_duplicate_case_id: str | None = None
    case_seed: int
    scan_profile: ScanProfile
    scan_parameters: tuple[ScanParameters, ...]
    original_pdf_path: str
    scanned_pdf_path: str
    input_path: str
    ground_truth_path: str
    scan_metadata_path: str
    sha256: dict[str, str]


class CorpusManifest(SyntheticModel):
    version: Literal["1"] = VERSION
    generator_version: str = GENERATOR_VERSION
    master_seed: int = MASTER_SEED
    dpi: int = 100
    case_count: int
    category_counts: dict[str, int]
    supplier_counts: dict[str, int]
    layout_counts: dict[str, int]
    entries: tuple[CorpusEntry, ...] = Field(min_length=70)

    @model_validator(mode="after")
    def counts_agree(self):
        if len(self.entries) != self.case_count or self.case_count != 70:
            raise ValueError("corpus requires exactly 70 entries")
        if self.category_counts != dict(Counter(e.category.value for e in self.entries)):
            raise ValueError("category counts differ from entries")
        if any(self.category_counts.get(c.value) != 10 for c in CATEGORIES):
            raise ValueError("each category needs ten entries")
        if self.supplier_counts != dict(Counter(e.supplier_code for e in self.entries)):
            raise ValueError("supplier counts differ from entries")
        if self.layout_counts != dict(Counter(e.layout.value for e in self.entries)):
            raise ValueError("layout counts differ from entries")
        return self


def _upc(base: str) -> str:
    weighted = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(base))
    return base + str((-weighted) % 10)


def _profile(category: CaseCategory, seed: int) -> ScanProfile:
    if category == CaseCategory.BLURRY_SCAN:
        return (ScanProfile.MODERATE_BLUR, ScanProfile.COMBINED)[seed % 2]
    return (ScanProfile.CLEAN, ScanProfile.MILD_BLUR, ScanProfile.ROTATION,
            ScanProfile.LOW_CONTRAST, ScanProfile.JPEG_ARTIFACTS)[seed % 5]


def build_case(category: CaseCategory, index: int, *, master_seed: int = MASTER_SEED,
               duplicate_source: SyntheticInvoiceInput | None = None) -> SyntheticCase:
    """A case's random choices depend only on its stable ID, never loop position."""
    if not 0 <= index < 10:
        raise ValueError("case index must be 0..9")
    case_id = f"corpus_{category.value}_{index + 1:02d}"
    rng = random.Random(derive_case_seed(master_seed, case_id))
    supplier = SUPPLIERS[(index + CATEGORIES.index(category)) % 3]
    if duplicate_source is not None:
        if category != CaseCategory.DUPLICATE_INVOICE or supplier != duplicate_source.supplier:
            raise ValueError("duplicate source must use the same supplier")
        products, lines = duplicate_source.products, duplicate_source.lines
        invoice_number = duplicate_source.invoice_number
        po_reference = duplicate_source.purchase_order_reference
    else:
        count = 2 + rng.randrange(3)
        products = []
        lines = []
        for number in range(1, count + 1):
            identifier_seed = derive_case_seed(master_seed, f"{case_id}_product_{number}")
            sku = f"FICT_{supplier.code[-1]}{index + 1:02d}_{CATEGORIES.index(category)}_P{number}"
            identifier = ({"upc": _upc(f"000{identifier_seed % 10**8:08d}")}
                          if number % 2 else {"ndc": f"{identifier_seed % 10**11:011d}"})
            products.append(SyntheticProduct(sku=sku, description=DESCRIPTIONS[rng.randrange(len(DESCRIPTIONS))],
                                             **identifier))
            quantity = rng.randint(3, 9) if category == CaseCategory.QUANTITY_MISMATCH and number == 1 else rng.randint(1, 9)
            unit_cost = Decimal(rng.randint(200, 8500)) / Decimal(100)
            lines.append(SyntheticLineItem(line_number=number, product_sku=sku,
                                           quantity=quantity, unit_cost=unit_cost,
                                           line_total=unit_cost * quantity))
        products, lines = tuple(products), tuple(lines)
        identity_seed = derive_case_seed(master_seed, case_id)
        invoice_number = f"{supplier.code}-INV-{identity_seed & 0xFFFFFFFF:08X}"
        po_reference = f"{supplier.code}-PO-{(identity_seed >> 32) & 0xFFFFFFFF:08X}"
    subtotal = sum((line.line_total for line in lines), Decimal(0))
    tax = (Decimal(rng.randrange(0, 1500)) / Decimal(100)).quantize(Decimal("0.0001"))
    totals = InvoiceTotals(subtotal=subtotal, tax=tax, total=subtotal + tax)
    invoice = SyntheticInvoiceInput(
        case_id=case_id, supplier=supplier, invoice_number=invoice_number,
        invoice_date=date(2029, 1, 1) + timedelta(days=rng.randrange(365)),
        purchase_order_reference=po_reference, products=products, lines=lines,
        displayed_totals=None if category == CaseCategory.MISSING_TOTALS else totals,
        scan_blur=category == CaseCategory.BLURRY_SCAN,
    )
    quantity_refs = ({1: max(1, lines[0].quantity - 2)}
                     if category == CaseCategory.QUANTITY_MISMATCH else {})
    price_refs = ({1: lines[0].unit_cost + Decimal("1.0000")}
                  if category == CaseCategory.PRICE_ANOMALY else {})
    truth = ExpectedGroundTruth(
        category=category, expected_supplier_code=supplier.code,
        expected_invoice_number=invoice_number, expected_invoice_date=invoice.invoice_date,
        expected_purchase_order_reference=po_reference, expected_products=products,
        expected_lines=lines, expected_totals=totals,
        duplicate_of_case_id=duplicate_source.case_id if duplicate_source else None,
        unknown_product_skus=(products[0].sku,) if category == CaseCategory.UNKNOWN_PRODUCT else (),
        po_quantities=quantity_refs, reference_unit_costs=price_refs,
    )
    return SyntheticCase(generation=invoice, ground_truth=truth)


def _trusted(case: SyntheticCase, *, prior: tuple[PriorInvoiceRecord, ...] = ()) -> CorpusTruth:
    invoice, truth = case.generation, case.ground_truth
    missing = set(truth.unknown_product_skus)
    products = tuple(ProductRecord(id=p.sku, name=p.description, upc=p.upc, ndc=p.ndc)
                     for p in invoice.products if p.sku not in missing)
    po_lines = tuple(PurchaseOrderLineRecord(
        id=f"{invoice.purchase_order_reference}:{line.line_number}", product_id=line.product_sku,
        ordered_quantity=truth.po_quantities.get(line.line_number, line.quantity),
        expected_unit_cost=truth.reference_unit_costs.get(line.line_number, line.unit_cost),
        received_quantity=0, receipt_history_complete=True,
    ) for line in invoice.lines if line.product_sku not in missing)
    expected_validation = ("missing_subtotal", "missing_tax", "missing_total") if truth.category == CaseCategory.MISSING_TOTALS else ()
    expected_reconciliation = {
        CaseCategory.QUANTITY_MISMATCH: ("po_quantity_mismatch",),
        CaseCategory.UNKNOWN_PRODUCT: ("unknown_product",),
        CaseCategory.PRICE_ANOMALY: ("po_price_mismatch",),
        CaseCategory.DUPLICATE_INVOICE: ("duplicate_invoice_identity", "exact_repeat_submission",
                                         "conflicting_invoice_document"),
    }.get(truth.category, ())
    return CorpusTruth(
        case=case, trusted_supplier=SupplierRecord(id=invoice.supplier.code,
                                                    code=invoice.supplier.code, name=invoice.supplier.name),
        trusted_products=products,
        trusted_po=PurchaseOrderRecord(id=invoice.purchase_order_reference,
                                       order_number=invoice.purchase_order_reference,
                                       supplier_id=invoice.supplier.code, status="open", lines=po_lines),
        prior_invoices=prior, expected_validation_codes=expected_validation,
        expected_reconciliation_codes=expected_reconciliation,
        duplicate_relationship="distinct_bytes_same_identity" if prior else None,
    )


def _write(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)
    return sha256(content).hexdigest()


def _json(value: SyntheticModel) -> bytes:
    return (value.model_dump_json(indent=2) + "\n").encode("utf-8")


def generate_corpus(root: Path, *, master_seed: int = MASTER_SEED, dpi: int = 100) -> CorpusManifest:
    """Generate only within a dedicated corpus root; never touches legacy outputs."""
    root = Path(root)
    if root.name != "corpus":
        raise ValueError("output directory must be named corpus")
    if any(part in {"evaluation", "ocr", "extraction", "validation", "reconciliation"}
           for part in root.resolve().parts):
        raise ValueError("corpus output must be separate from historical evaluation directories")
    if root.is_symlink():
        raise ValueError("corpus root must not be a symlink")
    entries = []
    generated: dict[str, tuple[SyntheticInvoiceInput, str]] = {}
    for category in CATEGORIES:
        for index in range(10):
            supplier_index = (index + CATEGORIES.index(category)) % 3
            related = None
            duplicate_source = None
            if category == CaseCategory.DUPLICATE_INVOICE:
                source_index = index + 1 if index < 9 else 1
                related = f"corpus_clean_{source_index + 1:02d}"
                duplicate_source = generated[related][0]
            case = build_case(category, index, master_seed=master_seed,
                              duplicate_source=duplicate_source)
            invoice = case.generation
            source = render_invoice_pdf(invoice)
            profile = _profile(category, derive_case_seed(master_seed, invoice.case_id))
            scan = scan_invoice(source, invoice, master_seed=master_seed, profile=profile, dpi=dpi)
            prior = ()
            if related:
                prior = (
                    PriorInvoiceRecord(id=f"prior:{related}", supplier_id=invoice.supplier.code,
                                       invoice_number_normalized=invoice.invoice_number,
                                       source_sha256=generated[related][1]),
                    PriorInvoiceRecord(id=f"prior:repeat:{invoice.case_id}", supplier_id=invoice.supplier.code,
                                       invoice_number_normalized=invoice.invoice_number,
                                       source_sha256=scan.metadata.scanned_pdf_sha256),
                )
            truth = _trusted(case, prior=prior)
            paths = {
                "original_pdf_path": f"original/{invoice.case_id}.pdf",
                "scanned_pdf_path": f"scanned/{invoice.case_id}.pdf",
                "input_path": f"inputs/{invoice.case_id}.json",
                "ground_truth_path": f"ground_truth/{invoice.case_id}.json",
                "scan_metadata_path": f"scan_metadata/{invoice.case_id}.json",
            }
            contents = {
                "original_pdf_path": source, "scanned_pdf_path": scan.pdf_bytes,
                "input_path": _json(invoice), "ground_truth_path": _json(truth),
                "scan_metadata_path": _json(scan.metadata),
            }
            hashes = {key: _write(root / path, contents[key]) for key, path in paths.items()}
            entries.append(CorpusEntry(
                case_id=invoice.case_id, category=category, supplier_code=invoice.supplier.code,
                layout=invoice.supplier.layout, related_duplicate_case_id=related,
                case_seed=derive_case_seed(master_seed, invoice.case_id), scan_profile=profile,
                scan_parameters=tuple(page.parameters for page in scan.metadata.pages),
                sha256=hashes, **paths,
            ))
            generated[invoice.case_id] = (invoice, scan.metadata.scanned_pdf_sha256)
    manifest = CorpusManifest(master_seed=master_seed, dpi=dpi, case_count=len(entries),
                              category_counts=dict(Counter(e.category.value for e in entries)),
                              supplier_counts=dict(Counter(e.supplier_code for e in entries)),
                              layout_counts=dict(Counter(e.layout.value for e in entries)),
                              entries=tuple(entries))
    _write(root / "manifest.json", _json(manifest))
    return manifest


def validate_manifest(root: Path) -> CorpusManifest:
    root = Path(root)
    manifest = CorpusManifest.model_validate_json((root / "manifest.json").read_text())
    ids = set()
    for entry in manifest.entries:
        if entry.case_id in ids:
            raise ValueError(f"duplicate case ID: {entry.case_id}")
        if entry.case_seed != derive_case_seed(manifest.master_seed, entry.case_id):
            raise ValueError(f"incorrect seed: {entry.case_id}")
        for key in ("original_pdf_path", "scanned_pdf_path", "input_path", "ground_truth_path", "scan_metadata_path"):
            relative = Path(getattr(entry, key))
            if (relative.is_absolute() or ".." in relative.parts or
                    not (root / relative).resolve().is_relative_to(root.resolve())):
                raise ValueError(f"unsafe artifact path: {relative}")
            if sha256((root / relative).read_bytes()).hexdigest() != entry.sha256[key]:
                raise ValueError(f"artifact hash mismatch: {relative}")
        invoice = SyntheticInvoiceInput.model_validate_json((root / entry.input_path).read_text())
        truth = CorpusTruth.model_validate_json((root / entry.ground_truth_path).read_text())
        scan = ScanMetadata.model_validate_json((root / entry.scan_metadata_path).read_text())
        if (invoice.case_id != entry.case_id or truth.case.generation != invoice or
                truth.case.ground_truth.category != entry.category or
                scan.source_case_id != entry.case_id or scan.degradation_type != entry.scan_profile or
                tuple(page.parameters for page in scan.pages) != entry.scan_parameters or
                scan.source_pdf_sha256 != entry.sha256["original_pdf_path"] or
                scan.scanned_pdf_sha256 != entry.sha256["scanned_pdf_path"]):
            raise ValueError(f"provenance mismatch: {entry.case_id}")
        if entry.category == CaseCategory.DUPLICATE_INVOICE:
            if entry.related_duplicate_case_id not in ids or truth.case.ground_truth.duplicate_of_case_id != entry.related_duplicate_case_id:
                raise ValueError(f"invalid duplicate relationship: {entry.case_id}")
            related = next(e for e in manifest.entries if e.case_id == entry.related_duplicate_case_id)
            earlier = SyntheticInvoiceInput.model_validate_json((root / related.input_path).read_text())
            if (earlier.supplier.code, earlier.invoice_number) != (invoice.supplier.code, invoice.invoice_number):
                raise ValueError(f"duplicate identity mismatch: {entry.case_id}")
            if related.sha256["scanned_pdf_path"] == entry.sha256["scanned_pdf_path"]:
                raise ValueError(f"expected distinct duplicate bytes: {entry.case_id}")
        elif entry.related_duplicate_case_id is not None:
            raise ValueError(f"unexpected duplicate relationship: {entry.case_id}")
        ids.add(entry.case_id)
    return manifest
