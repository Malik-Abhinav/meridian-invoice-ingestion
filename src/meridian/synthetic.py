"""Contracts for fictional invoice cases; no rendering or dataset generation."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictInt, ValidationInfo, field_validator, model_validator

from meridian.domain import require_money


def _exact_money(value: object, info: ValidationInfo) -> Decimal:
    if info.mode == "json" and isinstance(value, str):
        try:
            value = Decimal(value)
        except Exception as exc:
            raise ValueError("money must be a decimal string in JSON") from exc
    if not isinstance(value, Decimal):
        raise ValueError("money must be supplied as Decimal")
    return require_money(value)


Money = Annotated[
    Decimal,
    BeforeValidator(_exact_money),
    Field(max_digits=18, decimal_places=4),
]
PositiveInt = Annotated[StrictInt, Field(gt=0)]


class SyntheticModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CaseCategory(StrEnum):
    CLEAN = "clean"
    MISSING_TOTALS = "missing_totals"
    BLURRY_SCAN = "blurry_scan"
    QUANTITY_MISMATCH = "quantity_mismatch"
    DUPLICATE_INVOICE = "duplicate_invoice"
    UNKNOWN_PRODUCT = "unknown_product"
    PRICE_ANOMALY = "price_anomaly"


class SupplierLayout(StrEnum):
    TRADITIONAL = "traditional"
    COMPACT = "compact"
    PURCHASE_ORDER = "purchase_order"


class SyntheticSupplier(SyntheticModel):
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Z][A-Z0-9_]*$")
    name: str = Field(min_length=1, max_length=200)
    fictional: bool = Field(default=True)
    layout: SupplierLayout = SupplierLayout.TRADITIONAL

    @field_validator("fictional")
    @classmethod
    def require_fictional(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("supplier must be explicitly fictional")
        return value


class SyntheticProduct(SyntheticModel):
    sku: str = Field(min_length=1, max_length=40, pattern=r"^[A-Z][A-Z0-9_]*$")
    description: str = Field(min_length=1, max_length=200)
    upc: str | None = None
    ndc: str | None = None

    @field_validator("upc")
    @classmethod
    def valid_upc(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 12 or not value.isascii() or not value.isdecimal():
            raise ValueError("UPC must be 12 ASCII digits in UPC-A form")
        weighted = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(value[:11]))
        if (10 - weighted % 10) % 10 != int(value[-1]):
            raise ValueError("UPC-A check digit is invalid")
        return value

    @field_validator("ndc")
    @classmethod
    def valid_ndc(cls, value: str | None) -> str | None:
        if value is not None and (len(value) != 11 or not value.isascii() or not value.isdecimal()):
            raise ValueError("NDC must be 11 ASCII digits in normalized 5-4-2 form")
        return value

    @model_validator(mode="after")
    def has_identifier(self) -> Self:
        if self.upc is None and self.ndc is None:
            raise ValueError("product needs a UPC or NDC")
        return self


class SyntheticLineItem(SyntheticModel):
    line_number: PositiveInt
    product_sku: str = Field(min_length=1, max_length=40)
    quantity: PositiveInt
    unit_cost: Money
    line_total: Money

    @model_validator(mode="after")
    def correct_extension(self) -> Self:
        if self.line_total != self.unit_cost * self.quantity:
            raise ValueError("line_total must equal quantity times unit_cost")
        return self


class InvoiceTotals(SyntheticModel):
    subtotal: Money
    tax: Money
    total: Money

    @model_validator(mode="after")
    def correct_total(self) -> Self:
        if self.total != self.subtotal + self.tax:
            raise ValueError("total must equal subtotal plus tax")
        return self


class PrintedTotals(SyntheticModel):
    """Invoice-visible amounts for a deliberately inconsistent evaluation fixture."""

    kind: Literal["printed"]
    subtotal: Money
    tax: Money
    total: Money


class SyntheticInvoiceInput(SyntheticModel):
    """Only this model is passed to a future invoice renderer."""

    case_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    supplier: SyntheticSupplier
    invoice_number: str = Field(min_length=1, max_length=120)
    invoice_date: date
    purchase_order_reference: str | None = Field(default=None, min_length=1, max_length=80)
    products: tuple[SyntheticProduct, ...] = Field(min_length=1)
    lines: tuple[SyntheticLineItem, ...] = Field(min_length=1)
    displayed_totals: InvoiceTotals | PrintedTotals | None = None
    scan_blur: bool = False

    @model_validator(mode="after")
    def consistent_lines(self) -> Self:
        skus = [product.sku for product in self.products]
        numbers = [line.line_number for line in self.lines]
        if len(skus) != len(set(skus)) or len(numbers) != len(set(numbers)):
            raise ValueError("product SKUs and line numbers must be unique")
        if any(line.product_sku not in skus for line in self.lines):
            raise ValueError("each line must reference a supplied product")
        if isinstance(self.displayed_totals, InvoiceTotals) and self.displayed_totals.subtotal != sum(
            (line.line_total for line in self.lines), Decimal("0")
        ):
            raise ValueError("displayed subtotal must equal the sum of line totals")
        return self


class ExpectedGroundTruth(SyntheticModel):
    """Evaluation labels never enter the invoice renderer."""

    category: CaseCategory
    expected_supplier_code: str
    expected_invoice_number: str
    expected_invoice_date: date
    expected_purchase_order_reference: str | None = None
    expected_products: tuple[SyntheticProduct, ...] = Field(min_length=1)
    expected_lines: tuple[SyntheticLineItem, ...] = Field(min_length=1)
    expected_totals: InvoiceTotals
    duplicate_of_case_id: str | None = None
    unknown_product_skus: tuple[str, ...] = ()
    po_quantities: dict[int, PositiveInt] = Field(default_factory=dict)
    reference_unit_costs: dict[int, Money] = Field(default_factory=dict)

    @model_validator(mode="after")
    def category_evidence(self) -> Self:
        if self.category == CaseCategory.DUPLICATE_INVOICE and not self.duplicate_of_case_id:
            raise ValueError("duplicate cases require a source case ID")
        if self.category == CaseCategory.UNKNOWN_PRODUCT and not self.unknown_product_skus:
            raise ValueError("unknown-product cases require affected SKUs")
        if self.category == CaseCategory.QUANTITY_MISMATCH and not self.po_quantities:
            raise ValueError("quantity-mismatch cases require PO quantities")
        if self.category == CaseCategory.PRICE_ANOMALY and not self.reference_unit_costs:
            raise ValueError("price-anomaly cases require reference costs")
        return self


class SyntheticCase(SyntheticModel):
    generation: SyntheticInvoiceInput
    ground_truth: ExpectedGroundTruth

    @model_validator(mode="after")
    def consistent_truth(self) -> Self:
        source, truth = self.generation, self.ground_truth
        expected_identity = (
            truth.expected_supplier_code,
            truth.expected_invoice_number,
            truth.expected_invoice_date,
            truth.expected_purchase_order_reference,
            truth.expected_products,
            truth.expected_lines,
        )
        generated_identity = (
            source.supplier.code,
            source.invoice_number,
            source.invoice_date,
            source.purchase_order_reference,
            source.products,
            source.lines,
        )
        if expected_identity != generated_identity:
            raise ValueError("ground truth must identify the generated invoice and lines")
        if truth.expected_totals.subtotal != sum((line.line_total for line in source.lines), Decimal("0")):
            raise ValueError("ground-truth subtotal must equal line totals")
        if truth.category == CaseCategory.MISSING_TOTALS and source.displayed_totals is not None:
            raise ValueError("missing-totals cases must omit displayed totals")
        if truth.category == CaseCategory.BLURRY_SCAN and not source.scan_blur:
            raise ValueError("blurry-scan cases require the generation blur flag")
        if truth.category != CaseCategory.MISSING_TOTALS and source.displayed_totals is None:
            raise ValueError("only missing-totals cases may omit displayed totals")
        if source.displayed_totals is not None and truth.expected_totals != source.displayed_totals:
            raise ValueError("ground-truth totals must match displayed totals")
        if truth.category != CaseCategory.BLURRY_SCAN and source.scan_blur:
            raise ValueError("only blurry-scan cases may request blur")
        if truth.duplicate_of_case_id == source.case_id:
            raise ValueError("an invoice cannot duplicate itself")
        line_numbers = {line.line_number for line in source.lines}
        product_skus = {product.sku for product in source.products}
        annotated_lines = set(truth.po_quantities) | set(truth.reference_unit_costs)
        if not annotated_lines.issubset(line_numbers):
            raise ValueError("annotations must reference invoice line numbers")
        if not set(truth.unknown_product_skus).issubset(product_skus):
            raise ValueError("unknown-product annotations must reference invoice products")
        by_number = {line.line_number: line for line in source.lines}
        if truth.category == CaseCategory.QUANTITY_MISMATCH and not any(
            by_number[number].quantity != quantity for number, quantity in truth.po_quantities.items()
        ):
            raise ValueError("quantity-mismatch case needs a differing PO quantity")
        if truth.category == CaseCategory.PRICE_ANOMALY and not any(
            by_number[number].unit_cost != cost for number, cost in truth.reference_unit_costs.items()
        ):
            raise ValueError("price-anomaly case needs a differing reference cost")
        return self


class SyntheticDatasetPlan(SyntheticModel):
    master_seed: Annotated[StrictInt, Field(ge=0)]
    suppliers: tuple[SyntheticSupplier, SyntheticSupplier, SyntheticSupplier]
    category_counts: dict[CaseCategory, PositiveInt]

    @model_validator(mode="after")
    def sufficient_coverage(self) -> Self:
        if len({supplier.code for supplier in self.suppliers}) != 3:
            raise ValueError("plan requires three distinct fictional wholesalers")
        if set(self.category_counts) != set(CaseCategory):
            raise ValueError("plan requires every case category")
        if sum(self.category_counts.values()) < 60:
            raise ValueError("plan requires at least 60 cases")
        return self


def derive_case_seed(master_seed: int, case_id: str) -> int:
    """Stable, versioned 64-bit seed independent of Python's hash randomization."""
    if type(master_seed) is not int or master_seed < 0:
        raise ValueError("master_seed must be a nonnegative integer")
    if not isinstance(case_id, str) or not 1 <= len(case_id) <= 80 or not case_id.isascii() or not all(
        char.islower() or char.isdigit() or char in "_-" for char in case_id
    ) or not case_id[0].isalnum():
        raise ValueError("case_id must use lowercase ASCII letters, digits, _ or -")
    payload = f"meridian-synthetic-v1\0{master_seed}\0{case_id}".encode("ascii")
    return int.from_bytes(sha256(payload).digest()[:8], "big")
