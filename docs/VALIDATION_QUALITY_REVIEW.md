# Task 12 validation quality review

This review uses the nine stored offline Task 11 extractions and their OCR JSON. It does not alter the original PDFs, scans, ground truth, OCR, or extraction outputs. The mock provider is an offline test aid, not an LLM accuracy measurement.

## Clean A fixture trace

The original selectable-text PDF and fixture truth print two lines. Row 1 has quantity `2`, unit cost `4.1250`, and extension `8.2500`. Row 2, “Fable teal mock supply bundle” / NDC `00000010235`, prints quantity **`3`**, unit cost **`7.5000`**, and extension **`22.5000`**. Its correct extension is `3 × 7.5000 = 22.5000`; the invoice subtotal is `30.7500`.

The scanned PDF's stored OCR text has `Fable teal mock supply bundle 53 7.5000 22.5000`. The `53` is a recognition/segmentation error in an otherwise correctly associated row. The stored mock extraction preserves `source_row_order=2`, the description and NDC, quantity `53`, unit cost `7.5000`, and printed line total `22.5000`. Validation calculates `53 × 7.5000 = 397.5000` and emits `line_extension_mismatch` for row 2. It separately sums *printed* extensions, `8.2500 + 22.5000 = 30.7500`, so there is no subtotal finding. The mismatch is **OCR recognition propagated into extraction**, not a Task 11 row-association error or Task 12 calculation error. The finding is retained. A controlled correctly extracted clean invoice routes to `ready_for_po_matching` in unit tests.

## Confidence and evidence policy

For each field, `ocr_confidence_min` is the minimum Tesseract confidence across **all words in all cited evidence references**. Multiple references are flattened before taking the minimum. A cited word below the configurable `minimum_ocr_confidence` produces a warning and review for a critical present value. The default floor is **50**, inclusive: confidence exactly 50 does not trigger the warning. This is a conservative, uncalibrated heuristic, not a probability or estimated accuracy. Change it with `ValidationPolicy(minimum_ocr_confidence=Decimal(...))` or `scripts/run_validation.py --ocr-floor ...`. Provider-reported `medium` or `high` uncertainty is a separate warning; `low` or null is not. Missing required values and missing cited evidence on present critical values also require review. A low-confidence description alone does not block; a missing description is a review warning. Optional absent UPC or NDC does not block when the other identifier is present.

Task 12 now checks cited page numbers, word indexes, and Tesseract line IDs. When the full `ExtractionResult` is supplied, it also requires its source SHA-256 to match the OCR document. Passing a bare `ExtractedInvoice` is useful in controlled tests but cannot verify document identity because that model has no source hash. A location match is not semantic proof. A numeric value cited by one unique numeric OCR token is marked `supported`; a citation containing several different numbers is `not_evaluable` even if one equals the value, because a whole-row citation does not identify the column. If no cited numeric token equals a present numeric value, it is `conflicting` and requires review. This narrow token check does not prove row/label identity, and text/date semantics remain unverified.

### Threshold sensitivity

The table shows only cases with **no local findings** at each floor. It does not account for future PO, catalog, supplier, or duplicate checks.

| OCR floor | Ready for PO matching | Additional observation |
| ---: | --- | --- |
| 0 | `a_po_reference_03`, `b_clean_04`, `b_unknown_06`, `c_clean_07` | Low-confidence rule disabled; other cases still require review. |
| 25 | `a_po_reference_03`, `b_clean_04` | `b_unknown_06` and `c_clean_07` gain low-confidence warnings. |
| **50** | `a_po_reference_03` | `b_clean_04` also gains low-confidence warnings. |
| 75 | `a_po_reference_03` | More warnings, no decision change. |
| 90 | None | `a_po_reference_03` also gains low-confidence warnings. |

The fixture ID prefix `fixture_` is omitted in the table for space. At floor 50, the two seeded clean B/C fixtures receive review solely from this OCR rule; this is a conservative cost of the default. Lowering the threshold would change routing, but nine synthetic mock cases cannot justify tuning a production threshold. The clean A fixture remains in review at every floor because of its extracted quantity `53`.

## Rule and routing audit

Required missing headers, no lines, missing identifiers/quantity/cost/extension/totals, zero or negative quantities, prohibited zero or negative unit costs, negative printed line/tax/subtotal/total values, duplicate exact identifiers, and three independent arithmetic relationships have focused tests. Money is finite, at most four fractional places and 18 digits, and cannot enter extraction from a float or JSON numeric value; signed printed observations are allowed so validation can diagnose negatives. Arithmetic uses exact `Decimal` equality with zero tolerance and does not infer a mismatch when an operand is absent. Repeated product identifiers are warnings for review, not a claim of duplicate invoice rows. Findings carry stable codes, field name and source row order, observed values, and available OCR citations; ordering is deterministic. Frozen source models are not mutated.

Errors and warnings currently require review. The only declared `info` severity is reserved for future nonblocking findings; decision logic respects each finding's `manual_review_required` flag, so an informational finding need not block. `rejected` requires the explicit `reject_empty_invoice` policy and no extracted lines. `ready_for_po_matching` authorizes no inventory action. External reconciliation is always recorded as not evaluated.

## Nine-case evaluation at default floor 50

Counts show repeated field-specific findings. The results are unchanged from the initial Task 12 report; the review strengthened evidence handling and tests without editing stored extraction data.

| Fixture suffix | Decision | Arithmetic | Missing/unreadable | Confidence |
| --- | --- | --- | --- | --- |
| `a_clean_01` | needs_review | `line_extension_mismatch` (row 2) | — | — |
| `a_arithmetic_02` | needs_review | `subtotal_mismatch` | 3 missing identifiers, 1 missing quantity, 1 unreadable field | 2 low OCR warnings |
| `a_po_reference_03` | ready_for_po_matching | — | — | — |
| `b_clean_04` | needs_review | — | — | 6 low OCR warnings |
| `b_quantity_05` | needs_review | — | 4 missing costs, 4 missing line totals | 1 low OCR warning |
| `b_unknown_06` | needs_review | — | — | 3 low OCR warnings |
| `c_clean_07` | needs_review | — | — | 3 low OCR warnings |
| `c_price_08` | needs_review | — | invoice number/date, lines, subtotal, tax missing | — |
| `c_supplier_09` | needs_review | — | invoice date and 3 identifiers missing | — |

`a_po_reference_03` has a deliberately wrong PO reference; `b_quantity_05`, `b_unknown_06`, `c_price_08`, and `c_supplier_09` likewise require external PO or catalog checks for their seeded discrepancies. A local-ready outcome is therefore not an end-to-end clean classification. Missing extraction values may mask arithmetic discrepancies; the validator reports the missing values and skips only the unevaluable comparison.

## Remaining limits

The minimum OCR confidence aggregation is sensitive to a single weak word in a broad row citation. Neither this floor nor provider uncertainty is calibrated against real invoices. Numeric OCR tokens can be ambiguous even when location references are valid; text/date semantic checking and field-to-column association remain open. The nine cases are too few to establish review or error rates for deployment. No PO, supplier, catalog, duplicate-invoice, or inventory result is inferred here.
