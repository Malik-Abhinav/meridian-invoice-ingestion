# Synthetic invoice evaluation contract

Task 6 defines schemas and a plan; it does not create invoice files or a dataset.

## Planned corpus

- `SyntheticDatasetPlan` requires one nonnegative master seed, three distinct fictional wholesalers, all seven primary categories, and at least 60 planned cases. The category counts are a planning contract, not generated cases.
- Categories: clean, missing totals, blurry scan, quantity mismatch, duplicate invoice, unknown product, and price anomaly. Each case has one primary category. Quantity and price anomalies carry PO or reference-cost evidence; duplicate cases identify an earlier case; unknown-product cases identify affected synthetic SKUs.
- Suppliers, products, names, invoice numbers, prices, and dates must be invented for this project. `fictional=True` is a schema guard, but a human review of future catalog fixtures is still needed because a validator cannot prove that a name or identifier does not belong to a real business or product. No real pharmacy records or AYC materials belong in the corpus.

## Generation and evaluation boundary

`SyntheticCase.generation` contains only data a future renderer needs: supplier, invoice number/date, a product catalog, line items, optional displayed totals, and a blur request. `SyntheticCase.ground_truth` contains expected extracted fields and evaluation-only annotations. The renderer must accept `SyntheticInvoiceInput`, never the enclosing case or `ExpectedGroundTruth`. Unknown-product, duplicate, PO quantity, and reference-price labels must not appear on the rendered invoice merely because they are ground-truth annotations.

Displayed totals are absent only for the missing-totals category, while ground truth still records the mathematically expected totals. Line extensions equal quantity times unit cost; subtotal equals the sum of line extensions; total equals subtotal plus tax. Money uses finite nonnegative `Decimal` values with at most four fractional digits and 18 total digits. Floats and implicit string conversion are rejected. Invoice dates are dates, not timestamps.

## Identifiers and reproducibility

- UPC is canonical 12-digit ASCII UPC-A with a valid check digit. NDC is canonical 11-digit ASCII in 5-4-2 form. Products need at least one; either or both are allowed. Formatting and 10-digit NDC conversion belong to a later input-normalization step. A syntactically valid identifier does not prove a product is real or known to the database.
- `derive_case_seed(master_seed, case_id)` hashes `meridian-synthetic-v1`, the decimal master seed, and the case ID with SHA-256, then uses the first eight bytes as an unsigned 64-bit seed. Case IDs use lowercase ASCII letters, digits, `_`, and `-`. This avoids Python's process-randomized `hash()` and keeps one case stable when other cases are added. A future generator must use a local random number generator initialized with this derived seed and document its own algorithm/version if byte-for-byte reproducibility across generator versions is required.
- Future corpus verification should confirm at least 60 unique case IDs, all three wholesalers, category counts, fixture provenance, and that generation outputs contain no evaluation labels. For duplicates, it should compare rendered document identity and invoice identity against the referenced case. OCR and extraction scoring are separate future tasks.
