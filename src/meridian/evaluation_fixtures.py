"""Nine explicit, fictional invoice evaluation fixtures and their artifact manifest."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from meridian.pdf_invoices import render_invoice_pdf
from meridian.scanned_invoices import ScanProfile, scan_invoice
from meridian.synthetic import (
    InvoiceTotals,
    Money,
    PositiveInt,
    PrintedTotals,
    SupplierLayout,
    SyntheticInvoiceInput,
    SyntheticLineItem,
    SyntheticModel,
    SyntheticProduct,
    SyntheticSupplier,
    derive_case_seed,
)


class FixtureScenario(StrEnum):
    CLEAN = "clean"
    ARITHMETIC = "arithmetic_subtotal"
    QUANTITY = "po_quantity_mismatch"
    PRICE = "po_price_mismatch"
    PO_REFERENCE = "po_reference_mismatch"
    UNKNOWN_PRODUCT = "unknown_product"
    PO_SUPPLIER = "po_supplier_mismatch"


class ExpectedFinding(StrEnum):
    SUBTOTAL_MISMATCH = "subtotal_mismatch"
    PO_QUANTITY_MISMATCH = "po_quantity_mismatch"
    PO_PRICE_MISMATCH = "po_price_mismatch"
    PO_REFERENCE_MISMATCH = "po_reference_mismatch"
    UNKNOWN_PRODUCT = "unknown_product"
    PO_SUPPLIER_MISMATCH = "po_supplier_mismatch"


class ProcessingDecision(StrEnum):
    AUTO_POST_CANDIDATE = "auto_post_candidate"
    REVIEW_REQUIRED = "review_required"


class ExpectedPoLine(SyntheticModel):
    line_number: PositiveInt
    product_sku: str
    product_identifier: str
    quantity: PositiveInt
    unit_cost: Money


class SeededDiscrepancy(SyntheticModel):
    finding: ExpectedFinding
    field: str
    displayed_value: str
    expected_value: str


class FixtureGroundTruth(SyntheticModel):
    """Evaluation-only expected values; never passed to either PDF renderer."""

    case_id: str
    scenario: FixtureScenario
    displayed_invoice: SyntheticInvoiceInput
    expected_correct_totals: InvoiceTotals
    expected_po_reference: str
    expected_po_supplier_code: str
    expected_po_lines: tuple[ExpectedPoLine, ...]
    known_product_skus: tuple[str, ...]
    known_product_identifiers: tuple[str, ...]
    seeded_discrepancies: tuple[SeededDiscrepancy, ...]
    expected_validation_outcomes: tuple[ExpectedFinding, ...]
    expected_processing_decision: ProcessingDecision

    @model_validator(mode="after")
    def matching_case_id(self):
        if self.case_id != self.displayed_invoice.case_id:
            raise ValueError("ground truth case ID must match the invoice input")
        return self


class ManifestEntry(SyntheticModel):
    case_id: str
    supplier_code: str
    invoice_number: str
    purchase_order_reference: str
    scenario: FixtureScenario
    original_pdf_path: str
    scanned_pdf_path: str
    ground_truth_path: str
    scan_metadata_path: str
    degradation_profile: ScanProfile
    expected_validation_outcomes: tuple[ExpectedFinding, ...]
    expected_processing_decision: ProcessingDecision
    related_case_ids: tuple[str, ...] = ()


class FixtureManifest(SyntheticModel):
    version: Literal["1"] = "1"
    master_seed: int
    entries: tuple[ManifestEntry, ...] = Field(min_length=1)


@dataclass(frozen=True)
class FixtureCase:
    invoice: SyntheticInvoiceInput
    truth: FixtureGroundTruth
    profile: ScanProfile


@dataclass(frozen=True)
class _CaseSpec:
    case_id: str
    supplier: str
    scenario: FixtureScenario
    invoice_date: date
    quantities: tuple[int, ...]
    unit_costs: tuple[str, ...]
    profile: ScanProfile


_SUPPLIERS = {
    "a": SyntheticSupplier(code="FICT_A", name="Imaginary Harbor Wholesale", layout=SupplierLayout.TRADITIONAL),
    "b": SyntheticSupplier(code="FICT_B", name="Fable Ridge Distribution", layout=SupplierLayout.COMPACT),
    "c": SyntheticSupplier(code="FICT_C", name="Northstar Fictional Supply", layout=SupplierLayout.PURCHASE_ORDER),
}
_DESCRIPTIONS = (
    "Imaginary amber sample carton",
    "Fictional cobalt unit pack",
    "Imaginary violet display sleeve",
    "Fable teal mock supply bundle",
    "Fictional copper trial packet",
    "Imaginary indigo sample case",
)
_CASE_SPECS = (
    _CaseSpec("fixture_a_clean_01", "a", FixtureScenario.CLEAN, date(2026, 1, 8),
              (2, 3), ("4.1250", "7.5000"), ScanProfile.CLEAN),
    _CaseSpec("fixture_a_arithmetic_02", "a", FixtureScenario.ARITHMETIC, date(2026, 1, 12),
              (4, 1, 5), ("8.2500", "12.3750", "3.1000"), ScanProfile.MILD_BLUR),
    _CaseSpec("fixture_a_po_reference_03", "a", FixtureScenario.PO_REFERENCE, date(2026, 1, 18),
              (3, 2, 6, 1), ("5.6250", "9.2500", "2.7500", "16.4000"), ScanProfile.ROTATION),
    _CaseSpec("fixture_b_clean_04", "b", FixtureScenario.CLEAN, date(2026, 2, 3),
              (1, 4, 2), ("11.7500", "4.5000", "13.1250"), ScanProfile.CLEAN),
    _CaseSpec("fixture_b_quantity_05", "b", FixtureScenario.QUANTITY, date(2026, 2, 9),
              (3, 7, 2, 4), ("6.3750", "2.8750", "14.5000", "5.2500"), ScanProfile.LOW_CONTRAST),
    _CaseSpec("fixture_b_unknown_06", "b", FixtureScenario.UNKNOWN_PRODUCT, date(2026, 2, 14),
              (2, 5), ("18.1250", "3.6250"), ScanProfile.JPEG_ARTIFACTS),
    _CaseSpec("fixture_c_clean_07", "c", FixtureScenario.CLEAN, date(2026, 3, 2),
              (2, 1, 3, 4), ("7.8750", "22.5000", "5.1250", "2.2500"), ScanProfile.CLEAN),
    _CaseSpec("fixture_c_price_08", "c", FixtureScenario.PRICE, date(2026, 3, 7),
              (3, 2), ("17.5000", "9.3750"), ScanProfile.MODERATE_BLUR),
    _CaseSpec("fixture_c_supplier_09", "c", FixtureScenario.PO_SUPPLIER, date(2026, 3, 15),
              (1, 5, 2), ("24.7500", "4.8750", "12.2500"), ScanProfile.COMBINED),
)


def _upc(case_number: int, product_number: int, seed: int) -> str:
    base = f"000000{case_number:02d}{product_number:02d}{seed % 10}"
    weighted = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(base))
    return base + str((10 - weighted % 10) % 10)


def _product(spec: _CaseSpec, case_number: int, product_number: int, master_seed: int) -> SyntheticProduct:
    seed = derive_case_seed(master_seed, f"{spec.case_id}_product_{product_number}")
    identifier = (
        {"upc": _upc(case_number, product_number, seed)}
        if product_number % 2 else
        {"ndc": f"00000{case_number:02d}{product_number:02d}{seed % 100:02d}"}
    )
    return SyntheticProduct(
        sku=f"FICT_{spec.supplier.upper()}{case_number:02d}_P{product_number}",
        description=_DESCRIPTIONS[(case_number + product_number) % len(_DESCRIPTIONS)],
        **identifier,
    )


def _identifier(product: SyntheticProduct) -> str:
    identifier = product.upc or product.ndc
    assert identifier is not None  # SyntheticProduct requires at least one.
    return identifier


def _build_case(spec: _CaseSpec, case_number: int, master_seed: int) -> FixtureCase:
    supplier = _SUPPLIERS[spec.supplier]
    products = tuple(_product(spec, case_number, index, master_seed)
                     for index in range(1, len(spec.quantities) + 1))
    lines = tuple(
        SyntheticLineItem(
            line_number=index, product_sku=product.sku, quantity=quantity,
            unit_cost=Decimal(cost), line_total=quantity * Decimal(cost),
        )
        for index, (product, quantity, cost) in enumerate(
            zip(products, spec.quantities, spec.unit_costs, strict=True), 1
        )
    )
    correct_subtotal = sum((line.line_total for line in lines), Decimal("0"))
    tax = Decimal(case_number) / Decimal("2")
    correct_totals = InvoiceTotals(subtotal=correct_subtotal, tax=tax, total=correct_subtotal + tax)
    displayed_totals: InvoiceTotals | PrintedTotals = correct_totals
    if spec.scenario == FixtureScenario.ARITHMETIC:
        wrong_subtotal = correct_subtotal + Decimal("1.0000")
        displayed_totals = PrintedTotals(kind="printed", subtotal=wrong_subtotal,
                                         tax=tax, total=wrong_subtotal + tax)
    printed_po = f"FICT_{spec.supplier.upper()}-PO-{case_number:04d}"
    expected_po = printed_po
    if spec.scenario == FixtureScenario.PO_REFERENCE:
        printed_po = "FICT_A-PO-9999"
    invoice = SyntheticInvoiceInput(
        case_id=spec.case_id, supplier=supplier,
        invoice_number=f"FICT_{spec.supplier.upper()}-INV-{case_number:04d}",
        invoice_date=spec.invoice_date, purchase_order_reference=printed_po,
        products=products, lines=lines, displayed_totals=displayed_totals,
    )
    po_lines = [ExpectedPoLine(line_number=line.line_number, product_sku=line.product_sku,
                               product_identifier=_identifier(products[index]),
                               quantity=line.quantity, unit_cost=line.unit_cost)
                for index, line in enumerate(lines)]
    known_skus = [product.sku for product in products]
    known_identifiers = [_identifier(product) for product in products]
    po_supplier = supplier.code
    discrepancies: list[SeededDiscrepancy] = []
    if spec.scenario == FixtureScenario.ARITHMETIC:
        discrepancies.extend((
            SeededDiscrepancy(finding=ExpectedFinding.SUBTOTAL_MISMATCH, field="totals.subtotal",
                              displayed_value=str(displayed_totals.subtotal), expected_value=str(correct_subtotal)),
            SeededDiscrepancy(finding=ExpectedFinding.SUBTOTAL_MISMATCH, field="totals.total",
                              displayed_value=str(displayed_totals.total), expected_value=str(correct_totals.total)),
        ))
    elif spec.scenario == FixtureScenario.QUANTITY:
        po_lines[1] = po_lines[1].model_copy(update={"quantity": 5})
        discrepancies.append(SeededDiscrepancy(
            finding=ExpectedFinding.PO_QUANTITY_MISMATCH, field="lines[2].quantity",
            displayed_value=str(lines[1].quantity), expected_value="5",
        ))
    elif spec.scenario == FixtureScenario.PRICE:
        po_lines[0] = po_lines[0].model_copy(update={"unit_cost": Decimal("15.5000")})
        discrepancies.append(SeededDiscrepancy(
            finding=ExpectedFinding.PO_PRICE_MISMATCH, field="lines[1].unit_cost",
            displayed_value=str(lines[0].unit_cost), expected_value="15.5000",
        ))
    elif spec.scenario == FixtureScenario.PO_REFERENCE:
        discrepancies.append(SeededDiscrepancy(
            finding=ExpectedFinding.PO_REFERENCE_MISMATCH, field="purchase_order_reference",
            displayed_value=printed_po, expected_value=expected_po,
        ))
    elif spec.scenario == FixtureScenario.UNKNOWN_PRODUCT:
        expected_sku = "FICT_B06_CATALOG_P2"
        expected_identifier = "00000060288"
        po_lines[1] = po_lines[1].model_copy(update={"product_sku": expected_sku,
                                              "product_identifier": expected_identifier})
        known_skus[1] = expected_sku
        known_identifiers[1] = expected_identifier
        discrepancies.append(SeededDiscrepancy(
            finding=ExpectedFinding.UNKNOWN_PRODUCT, field="products[2].ndc",
            displayed_value=products[1].ndc, expected_value=expected_identifier,
        ))
    elif spec.scenario == FixtureScenario.PO_SUPPLIER:
        po_supplier = "FICT_B"
        discrepancies.append(SeededDiscrepancy(
            finding=ExpectedFinding.PO_SUPPLIER_MISMATCH, field="supplier.code",
            displayed_value=supplier.code, expected_value=po_supplier,
        ))
    findings = tuple(dict.fromkeys(discrepancy.finding for discrepancy in discrepancies))
    truth = FixtureGroundTruth(
        case_id=spec.case_id, scenario=spec.scenario, displayed_invoice=invoice,
        expected_correct_totals=correct_totals, expected_po_reference=expected_po,
        expected_po_supplier_code=po_supplier, expected_po_lines=tuple(po_lines),
        known_product_skus=tuple(known_skus),
        known_product_identifiers=tuple(known_identifiers),
        seeded_discrepancies=tuple(discrepancies),
        expected_validation_outcomes=findings,
        expected_processing_decision=(ProcessingDecision.REVIEW_REQUIRED if findings
                                      else ProcessingDecision.AUTO_POST_CANDIDATE),
    )
    return FixtureCase(invoice=invoice, truth=truth, profile=spec.profile)


def build_fixture_cases(master_seed: int = 2029) -> tuple[FixtureCase, ...]:
    """Build nine fixed scenarios; the seed varies synthetic identifiers and scans."""
    return tuple(_build_case(spec, index, master_seed) for index, spec in enumerate(_CASE_SPECS, 1))


def generate_fixture_set(output_dir: Path, *, master_seed: int = 2029, dpi: int = 150) -> FixtureManifest:
    """Write nine PDF pairs, evaluation-only truth, and one relative-path manifest."""
    output_dir = Path(output_dir)
    for subdirectory in ("original", "scanned", "ground_truth", "scan_metadata"):
        (output_dir / subdirectory).mkdir(parents=True, exist_ok=True)
    entries = []
    for case in build_fixture_cases(master_seed):
        case_id = case.invoice.case_id
        source_pdf = render_invoice_pdf(case.invoice)
        scan = scan_invoice(source_pdf, case.invoice, master_seed=master_seed, profile=case.profile, dpi=dpi)
        paths = {
            "original_pdf_path": f"original/{case_id}.pdf",
            "scanned_pdf_path": f"scanned/{case_id}.pdf",
            "ground_truth_path": f"ground_truth/{case_id}.json",
            "scan_metadata_path": f"scan_metadata/{case_id}.json",
        }
        (output_dir / paths["original_pdf_path"]).write_bytes(source_pdf)
        (output_dir / paths["scanned_pdf_path"]).write_bytes(scan.pdf_bytes)
        (output_dir / paths["ground_truth_path"]).write_text(
            case.truth.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (output_dir / paths["scan_metadata_path"]).write_text(
            scan.metadata.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        entries.append(ManifestEntry(
            case_id=case_id, supplier_code=case.invoice.supplier.code,
            invoice_number=case.invoice.invoice_number,
            purchase_order_reference=case.invoice.purchase_order_reference,
            scenario=case.truth.scenario, degradation_profile=case.profile,
            expected_validation_outcomes=case.truth.expected_validation_outcomes,
            expected_processing_decision=case.truth.expected_processing_decision,
            **paths,
        ))
    manifest = FixtureManifest(master_seed=master_seed, entries=tuple(entries))
    (output_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return manifest
