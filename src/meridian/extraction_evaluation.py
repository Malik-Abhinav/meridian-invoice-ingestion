"""Score observed extraction against invoice-visible fixture values only."""

from collections import Counter
from pydantic import Field

from meridian.evaluation_fixtures import FixtureGroundTruth
from meridian.extraction import ExtractedInvoice
from meridian.synthetic import SupplierLayout, SyntheticModel


class FieldScore(SyntheticModel):
    correct: int = Field(ge=0)
    expected: int = Field(ge=0)
    missing: int = Field(ge=0)
    unsupported: int = Field(ge=0)


class InvoiceExtractionEvaluation(SyntheticModel):
    case_id: str
    fields: dict[str, FieldScore]
    matched_lines: int
    expected_lines: int
    extra_lines: int
    unmatched_line_numbers: tuple[int, ...]
    extra_source_row_orders: tuple[int, ...]


class AggregateExtractionEvaluation(SyntheticModel):
    invoices: tuple[InvoiceExtractionEvaluation, ...]
    fields: dict[str, FieldScore]
    matched_lines: int
    expected_lines: int
    extra_lines: int


def _score(observed, expected) -> FieldScore:
    if expected is None:
        return FieldScore(correct=0, expected=0, missing=0,
                          unsupported=int(observed is not None))
    if observed is None:
        return FieldScore(correct=0, expected=1, missing=1, unsupported=0)
    if observed == expected:
        return FieldScore(correct=1, expected=1, missing=0, unsupported=0)
    return FieldScore(correct=0, expected=1, missing=0, unsupported=1)


def evaluate_extraction(invoice: ExtractedInvoice,
                        truth: FixtureGroundTruth) -> InvoiceExtractionEvaluation:
    """Match printed fields; never use corrected PO/catalog/validation values."""
    printed = truth.displayed_invoice
    expected_totals = printed.displayed_totals
    fields = {
        "supplier_name": _score(invoice.supplier_name.value, printed.supplier.name),
        "invoice_number": _score(invoice.invoice_number.value, printed.invoice_number),
        "invoice_date": _score(invoice.invoice_date.value, printed.invoice_date),
        "purchase_order_reference": _score(invoice.purchase_order_reference.value,
                                            printed.purchase_order_reference),
        "printed_subtotal": _score(invoice.printed_subtotal.value,
                                   expected_totals.subtotal if expected_totals else None),
        "printed_tax": _score(invoice.printed_tax.value,
                              expected_totals.tax if expected_totals else None),
        "printed_total": _score(invoice.printed_total.value,
                                expected_totals.total if expected_totals else None),
    }
    products = {product.sku: product for product in printed.products}
    unmatched = list(zip(printed.lines, (products[line.product_sku] for line in printed.lines)))
    scores: dict[str, list[FieldScore]] = {key: [] for key in (
        "line_number", "description", "product_identifier", "quantity", "unit_cost",
        "printed_line_total")}
    printed_numbers = printed.supplier.layout == SupplierLayout.PURCHASE_ORDER
    matched = 0
    extras: list[int] = []
    for observed in invoice.lines:
        candidates = []
        for index, (expected, product) in enumerate(unmatched):
            identifier = product.upc or product.ndc
            weight = (
                3 * int(identifier is not None and identifier in
                        (observed.upc.value, observed.ndc.value)) +
                2 * int(observed.description.value is not None and
                        observed.description.value == product.description) +
                int(printed_numbers and observed.line_number.value is not None and
                    observed.line_number.value == expected.line_number)
            )
            if weight:
                candidates.append((weight, -index, index, expected, product))
        if not candidates:
            extras.append(observed.source_row_order)
            continue
        _, _, index, expected, product = max(candidates)
        unmatched.pop(index)
        matched += 1
        for key, actual, target in (
            ("line_number", observed.line_number.value,
             expected.line_number if printed_numbers else None),
            ("description", observed.description.value, product.description),
            ("product_identifier", observed.upc.value or observed.ndc.value,
             product.upc or product.ndc),
            ("quantity", observed.quantity.value, expected.quantity),
            ("unit_cost", observed.unit_cost.value, expected.unit_cost),
            ("printed_line_total", observed.printed_line_total.value, expected.line_total),
        ):
            scores[key].append(_score(actual, target))
    for expected, product in unmatched:
        for key in scores:
            target = expected.line_number if key == "line_number" and printed_numbers else (
                None if key == "line_number" else True)
            scores[key].append(_score(None, target))
    for key, values in scores.items():
        fields[key] = FieldScore(correct=sum(v.correct for v in values),
                                 expected=sum(v.expected for v in values),
                                 missing=sum(v.missing for v in values),
                                 unsupported=sum(v.unsupported for v in values))
    return InvoiceExtractionEvaluation(case_id=truth.case_id, fields=fields,
                                       matched_lines=matched, expected_lines=len(printed.lines),
                                       extra_lines=len(extras),
                                       unmatched_line_numbers=tuple(line.line_number for line, _ in unmatched),
                                       extra_source_row_orders=tuple(extras))


def aggregate_extractions(cases: tuple[InvoiceExtractionEvaluation, ...]
                          ) -> AggregateExtractionEvaluation:
    totals = {name: Counter() for case in cases for name in case.fields}
    for case in cases:
        for name, score in case.fields.items():
            totals[name].update(score.model_dump())
    return AggregateExtractionEvaluation(
        invoices=cases,
        fields={name: FieldScore(**counts) for name, counts in totals.items()},
        matched_lines=sum(case.matched_lines for case in cases),
        expected_lines=sum(case.expected_lines for case in cases),
        extra_lines=sum(case.extra_lines for case in cases),
    )
