"""Contract checks; these fixtures are invented and are not a dataset."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from meridian.synthetic import (
    CaseCategory,
    ExpectedGroundTruth,
    InvoiceTotals,
    SyntheticCase,
    SyntheticDatasetPlan,
    SyntheticInvoiceInput,
    SyntheticLineItem,
    SyntheticProduct,
    SyntheticSupplier,
    derive_case_seed,
)


def sample_case(category: CaseCategory = CaseCategory.CLEAN) -> SyntheticCase:
    supplier = SyntheticSupplier(code="FICTIONAL_A", name="Imaginary Harbor Wholesale")
    product = SyntheticProduct(sku="FICTIONAL_P1", description="Imaginary tablets", upc="000000000000")
    line = SyntheticLineItem(
        line_number=1, product_sku=product.sku, quantity=2,
        unit_cost=Decimal("1.2345"), line_total=Decimal("2.4690"),
    )
    totals = InvoiceTotals(subtotal=Decimal("2.4690"), tax=Decimal("0.20"), total=Decimal("2.6690"))
    generation = SyntheticInvoiceInput(
        case_id="case_001", supplier=supplier, invoice_number="FICT-001",
        invoice_date=date(2026, 1, 1), products=(product,), lines=(line,),
        displayed_totals=None if category == CaseCategory.MISSING_TOTALS else totals,
        scan_blur=category == CaseCategory.BLURRY_SCAN,
    )
    truth = ExpectedGroundTruth(
        category=category, expected_supplier_code=supplier.code,
        expected_invoice_number=generation.invoice_number,
        expected_invoice_date=generation.invoice_date,
        expected_products=(product,), expected_lines=(line,),
        expected_totals=totals,
        duplicate_of_case_id="case_000" if category == CaseCategory.DUPLICATE_INVOICE else None,
        unknown_product_skus=(product.sku,) if category == CaseCategory.UNKNOWN_PRODUCT else (),
        po_quantities={1: 3} if category == CaseCategory.QUANTITY_MISMATCH else {},
        reference_unit_costs={1: Decimal("1.0000")} if category == CaseCategory.PRICE_ANOMALY else {},
    )
    return SyntheticCase(generation=generation, ground_truth=truth)


@pytest.mark.parametrize("category", list(CaseCategory))
def test_all_categories_have_valid_contracts(category: CaseCategory) -> None:
    case = sample_case(category)
    assert case.ground_truth.category == category
    assert case.generation.case_id == "case_001"


def test_product_identifiers_accept_upc_or_ndc_and_reject_invalid_values() -> None:
    upc = SyntheticProduct(sku="A", description="Fictional item", upc="000000000000")
    ndc = SyntheticProduct(sku="B", description="Fictional item", ndc="00000000000")
    assert upc.ndc is None and ndc.upc is None
    for identifiers in ({}, {"upc": "000000000001"}, {"upc": "０00000000000"},
                        {"ndc": "0000-0000-00"}, {"ndc": "0000000000"}):
        with pytest.raises(ValidationError):
            SyntheticProduct(sku="C", description="Fictional item", **identifiers)


def test_money_is_exact_and_no_floats_or_excess_scale() -> None:
    assert sample_case().generation.lines[0].unit_cost == Decimal("1.2345")
    for invalid in (1.25, "1.25", Decimal("NaN"), Decimal("-1"), Decimal("1.23456")):
        with pytest.raises(ValidationError):
            SyntheticLineItem(line_number=1, product_sku="A", quantity=1,
                              unit_cost=invalid, line_total=Decimal("1.25"))
    with pytest.raises(ValidationError):
        SyntheticLineItem(line_number=1, product_sku="A", quantity=2,
                          unit_cost=Decimal("1.00"), line_total=Decimal("3.00"))
    with pytest.raises(ValidationError):
        InvoiceTotals(subtotal=Decimal("1"), tax=Decimal("0.1"), total=Decimal("1.2"))


def test_invoice_rejects_unreferenced_products_and_duplicate_lines() -> None:
    source = sample_case().generation
    with pytest.raises(ValidationError):
        SyntheticInvoiceInput.model_validate(source.model_dump() | {
            "lines": [source.lines[0].model_dump()] * 2
        })
    with pytest.raises(ValidationError):
        SyntheticInvoiceInput.model_validate(source.model_dump() | {
            "lines": [{**source.lines[0].model_dump(), "product_sku": "ABSENT"}]
        })
    with pytest.raises(ValidationError):
        SyntheticInvoiceInput.model_validate(source.model_dump() | {
            "lines": [{**source.lines[0].model_dump(), "quantity": 0}]
        })


def test_truth_is_separate_and_cannot_leak_through_generation_schema() -> None:
    case = sample_case(CaseCategory.UNKNOWN_PRODUCT)
    assert "ground_truth" not in case.generation.model_dump()
    assert "category" not in case.generation.model_dump()
    assert "unknown_product_skus" not in case.generation.model_dump()
    with pytest.raises(ValidationError):
        SyntheticInvoiceInput.model_validate(case.generation.model_dump() | {
            "ground_truth": case.ground_truth.model_dump()
        })
    with pytest.raises(ValidationError):
        SyntheticCase.model_validate({"generation": case.generation.model_dump(),
                                      "ground_truth": case.ground_truth.model_dump() | {
                                          "expected_supplier_code": "WRONG"
                                      }})
    with pytest.raises(ValidationError):
        SyntheticCase.model_validate({"generation": case.generation.model_dump(),
                                      "ground_truth": case.ground_truth.model_dump() | {
                                          "expected_invoice_date": date(2026, 1, 2)
                                      }})
    with pytest.raises(ValidationError):
        SyntheticCase.model_validate({"generation": case.generation.model_dump(),
                                      "ground_truth": case.ground_truth.model_dump() | {
                                          "expected_totals": {
                                              "subtotal": Decimal("2.4690"),
                                              "tax": Decimal("0.30"),
                                              "total": Decimal("2.7690"),
                                          }
                                      }})


def test_category_requires_corresponding_annotations() -> None:
    clean = sample_case()
    for category in (CaseCategory.DUPLICATE_INVOICE, CaseCategory.UNKNOWN_PRODUCT,
                     CaseCategory.QUANTITY_MISMATCH, CaseCategory.PRICE_ANOMALY):
        with pytest.raises(ValidationError):
            ExpectedGroundTruth.model_validate(clean.ground_truth.model_dump() | {"category": category})
    with pytest.raises(ValidationError):
        SyntheticCase(generation=clean.generation, ground_truth=sample_case(CaseCategory.MISSING_TOTALS).ground_truth)
    with pytest.raises(ValidationError):
        SyntheticCase(generation=clean.generation, ground_truth=sample_case(CaseCategory.BLURRY_SCAN).ground_truth)
    quantity_case = sample_case(CaseCategory.QUANTITY_MISMATCH)
    with pytest.raises(ValidationError):
        SyntheticCase.model_validate(quantity_case.model_dump() | {
            "ground_truth": quantity_case.ground_truth.model_dump() | {"po_quantities": {1: 2}}
        })
    price_case = sample_case(CaseCategory.PRICE_ANOMALY)
    with pytest.raises(ValidationError):
        SyntheticCase.model_validate(price_case.model_dump() | {
            "ground_truth": price_case.ground_truth.model_dump() | {
                "reference_unit_costs": {1: Decimal("1.2345")}
            }
        })


def test_seed_derivation_is_stable_and_case_specific() -> None:
    assert derive_case_seed(42, "case_001") == derive_case_seed(42, "case_001")
    assert derive_case_seed(42, "case_001") != derive_case_seed(42, "case_002")
    assert derive_case_seed(42, "case_001") != derive_case_seed(43, "case_001")
    assert derive_case_seed(42, "case_001") == 12337763273153052967
    for invalid in (-1, True, 1.2):
        with pytest.raises(ValueError):
            derive_case_seed(invalid, "case_001")
    with pytest.raises(ValueError):
        derive_case_seed(42, "../case_001")


def test_dataset_plan_requires_three_wholesalers_and_sixty_cases() -> None:
    suppliers = tuple(SyntheticSupplier(code=f"FICT_{i}", name=f"Imaginary Wholesaler {i}") for i in range(3))
    counts = dict.fromkeys(CaseCategory, 9)
    plan = SyntheticDatasetPlan(master_seed=42, suppliers=suppliers, category_counts=counts)
    assert sum(plan.category_counts.values()) == 63
    with pytest.raises(ValidationError):
        SyntheticDatasetPlan(master_seed=42, suppliers=suppliers, category_counts=dict.fromkeys(CaseCategory, 8))
    with pytest.raises(ValidationError):
        SyntheticDatasetPlan(master_seed=42, suppliers=(suppliers[0],) * 3, category_counts=counts)
    with pytest.raises(ValidationError):
        SyntheticDatasetPlan(master_seed=42, suppliers=suppliers,
                             category_counts={CaseCategory.CLEAN: 60})
    with pytest.raises(ValidationError):
        SyntheticSupplier(code="FICT_4", name="Imaginary Wholesaler 4", fictional=False)
