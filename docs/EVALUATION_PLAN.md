# Synthetic invoice evaluation contract

Task 6 defined schemas and a plan. Tasks 7 and 8 added three clean review PDFs and seven scanned review variants. Task 9 adds nine evaluation fixtures; the planned 60-invoice corpus has not been generated.

## Planned corpus

- `SyntheticDatasetPlan` requires one nonnegative master seed, three distinct fictional wholesalers, all seven primary categories, and at least 60 planned cases. The category counts are a planning contract, not generated cases.
- Categories: clean, missing totals, blurry scan, quantity mismatch, duplicate invoice, unknown product, and price anomaly. Each case has one primary category. Quantity and price anomalies carry PO or reference-cost evidence; duplicate cases identify an earlier case; unknown-product cases identify affected synthetic SKUs.
- Suppliers, products, names, invoice numbers, prices, and dates must be invented for this project. `fictional=True` is a schema guard, but a human review of future catalog fixtures is still needed because a validator cannot prove that a name or identifier does not belong to a real business or product. No real pharmacy records or AYC materials belong in the corpus.

## Generation and evaluation boundary

`SyntheticCase.generation` contains only data a future renderer needs: supplier, invoice number/date, a product catalog, line items, optional displayed totals, and a blur request. `SyntheticCase.ground_truth` contains expected extracted fields and evaluation-only annotations. The renderer must accept `SyntheticInvoiceInput`, never the enclosing case or `ExpectedGroundTruth`. Unknown-product, duplicate, PO quantity, and reference-price labels must not appear on the rendered invoice merely because they are ground-truth annotations.

Displayed totals are absent only for the missing-totals category, while ground truth still records the mathematically expected totals. Ordinary invoice line extensions equal quantity times unit cost; subtotal equals the sum of line extensions; total equals subtotal plus tax. Task 9 uses an explicit `PrintedTotals` type solely for deliberately wrong invoice-visible totals. Money uses finite nonnegative `Decimal` values with at most four fractional digits and 18 total digits. Python construction rejects floats and implicit string conversion. Serialized JSON accepts decimal strings for lossless round-tripping, but rejects JSON numbers for money. Invoice dates are dates, not timestamps.

## Identifiers and reproducibility

- UPC is canonical 12-digit ASCII UPC-A with a valid check digit. NDC is canonical 11-digit ASCII in 5-4-2 form. Products need at least one; either or both are allowed. Formatting and 10-digit NDC conversion belong to a later input-normalization step. A syntactically valid identifier does not prove a product is real or known to the database.
- `derive_case_seed(master_seed, case_id)` hashes `meridian-synthetic-v1`, the decimal master seed, and the case ID with SHA-256, then uses the first eight bytes as an unsigned 64-bit seed. Case IDs use lowercase ASCII letters, digits, `_`, and `-`. This avoids Python's process-randomized `hash()` and keeps one case stable when other cases are added. A future generator must use a local random number generator initialized with this derived seed and document its own algorithm/version if byte-for-byte reproducibility across generator versions is required.
- Future corpus verification should confirm at least 60 unique case IDs, all three wholesalers, category counts, fixture provenance, and that generation outputs contain no evaluation labels. For duplicates, it should compare rendered document identity and invoice identity against the referenced case. OCR and extraction scoring are separate future tasks.

## PDF layout boundary (Task 7)

`SyntheticSupplier.layout` selects one of three text-based layouts. `SyntheticInvoiceInput.purchase_order_reference` is optional and has an evaluation counterpart in `ExpectedGroundTruth`; when absent, the PDF omits the PO section. Displayed totals are omitted when absent. The renderer receives only the generation input and does not read case category, duplicate links, unknown-product labels, PO quantity expectations, or reference costs.

The three sample PDFs under `output/pdf/` are visual review fixtures, one per layout. They do not count toward the future 60-case corpus. Long rows and page overflow use flowing tables with repeated headers. This text renderer does not apply `scan_blur`; a separate scan profile controls Task 8 rasterization.

## Scanned-image boundary (Task 8)

`scan_invoice` accepts only `SyntheticInvoiceInput`, the matching deterministic clean PDF bytes, a master seed, and an explicit `ScanProfile`. It rejects a mismatched source. It does not access `ExpectedGroundTruth`. The clean PDF remains selectable and unchanged; the scan PDF has one raster image per page and no selectable page text. The JSON sidecar records source and output SHA-256 hashes so the paired artifacts can be checked without embedding evaluation labels in the scan.

The seven review scans under `output/scans/` exercise all profiles at 150 DPI across all three suppliers. `derive_case_seed` supplies the case seed; a versioned SHA-256 derivation gives each page its own seed, which resolves the small rotation angle. Blur, contrast, and JPEG settings are fixed per profile and recorded for each page. Rotation expands the page image with white margins to avoid clipping. The same profile and seed produce identical PDF bytes and metadata. The `scan_blur` field remains a case-generation hint; a later corpus builder must map case category and hint to the explicit scan profile.

## Nine evaluation fixtures (Task 9)

Run `python scripts/generate_evaluation_fixtures.py` to recreate `output/evaluation/` with fixed master seed 2029 and 150 DPI. The nine case IDs are stable. Each fictional supplier has one clean case, one data or arithmetic discrepancy, and one matching or identity discrepancy. The scanner reuses the seven existing profiles; every case gets one selectable-text original and one image-only scanned PDF. PDF inputs contain only printed values. Evaluation-only JSON holds the expected PO reference, supplier, lines, known product SKUs, mathematically correct totals, seeded differences, expected findings, and a conservative processing decision. No discrepancy explanation is printed on a PDF.

| Case | Scenario | Expected finding | Scan profile |
| --- | --- | --- | --- |
| `fixture_a_clean_01` | clean | none | clean raster |
| `fixture_a_arithmetic_02` | printed subtotal and total exceed the line sum by 1.0000 | subtotal mismatch | mild blur |
| `fixture_a_po_reference_03` | printed PO reference differs from expected PO | PO reference mismatch | rotation |
| `fixture_b_clean_04` | clean | none | clean raster |
| `fixture_b_quantity_05` | invoice quantity 7 versus PO quantity 5 | PO quantity mismatch | low contrast |
| `fixture_b_unknown_06` | printed NDC absent from the expected catalog and PO line | unknown product | JPEG artifacts |
| `fixture_c_clean_07` | clean | none | clean raster |
| `fixture_c_price_08` | invoice unit cost 17.5000 versus PO cost 15.5000 | PO price mismatch | moderate blur |
| `fixture_c_supplier_09` | invoice supplier C differs from expected PO supplier B | PO supplier mismatch | combined |

The manifest at `output/evaluation/manifest.json` maps each case to its two PDFs, ground-truth JSON, scan-metadata JSON, profile, scenario, expected findings, and processing decision. Paths are relative to the manifest directory. `related_case_ids` is present as an empty list so later duplicate and idempotency cases can reference existing cases without changing the manifest shape. Clean cases are marked `auto_post_candidate` for evaluation routing only. All six discrepant cases require review. The fixture does not approve or post invoices; future validation must still prove an invoice eligible.

Expected PO data are fixture evidence, not database purchase-order rows. The PO quantity example represents an ordered quantity and does not model cumulative partial receipts. The unknown-product mismatch is based on the printed NDC, not merely an internal SKU. The nine cases are separate from the three approved Task 7 PDFs and seven Task 8 scan samples. Synthetic identifiers are varied and syntactically valid; they are not claims that products exist in a real registry.

## OCR baseline (Task 10)

OCR accepts the nine existing scanned PDFs and emits separate positional JSON, whitespace-normalized text, and evaluation reports under `output/ocr/`. It does not receive the ground-truth objects during extraction. Tesseract's page/block/paragraph/line/word IDs preserve the engine's reading order; bounding boxes and confidence remain attached to each word. Grayscale is the only default image transformation. Contrast, median denoising, thresholding, and projection-based small-angle deskewing are optional and must be chosen deliberately.

CER and WER use whitespace-normalized selectable text from each original PDF as the *printed transcription reference*, because fixture ground truth contains fields rather than a complete layout-aware transcript. The original text includes headings, labels, and footers; sequence-based CER/WER therefore also penalize different reading orders. Ground truth supplies the independent key-field reference, using invoice-visible values (including deliberately wrong printed totals or identifiers), not expected PO corrections. Exact-value presence is a recognition measure, not semantic extraction or invoice validation. Per-invoice failures are retained in the aggregate report.

The first Docker run used Tesseract 5.5.0 with English at 200 DPI and PSM 3. All nine cases completed. Aggregate CER was 0.4018 and WER was 0.4360. The compact supplier's quantity-mismatch scan had high sequence error despite recognizing all expected values because Tesseract read columns in a different order. The moderately blurred C price case lost most lines. Optional contrast enhancement improved that one scan in a spot check, but remains off by default to protect clean scans. These observations are a baseline, not tuned extraction results.

The focused evaluation review adds a token multiset dissimilarity metric and benchmarks five uniform settings across all nine scans. Reference and OCR text both collapse whitespace and preserve case and punctuation. Aggregate CER/WER are micro ratios of total edits to total reference characters/words. For token multisets, let R and O count each exact whitespace token in reference and OCR; missing is the sum of positive R−O, extra the sum of positive O−R, and dissimilarity is `(missing+extra)/(total reference tokens+total OCR tokens)`. This preserves duplicate counts and ignores order; it is not conventional WER. The baseline aggregate is 0.0945. See [OCR_EVALUATION_REVIEW.md](OCR_EVALUATION_REVIEW.md) for configuration comparisons, exact token failures, and interpretation. Ground-truth field presence remains separate from field assignment.

## Structured extraction baseline (Task 11)

`ExtractedInvoice` is separate from generation contracts and permits null, duplicate, missing, and inconsistent observations. Each non-null value must cite existing OCR words on a valid page and Tesseract line ID. Money uses decimal strings with up to four fractional digits. The extraction provider receives only OCR page text, line groups, word positions, and confidence; evaluation-only PO, catalog, validation findings, and decisions are loaded after extraction. The offline mock returns deterministic OCR-only observations and reports null for unsupported data. Its uncertainty is null because it is not an LLM. A real provider can report low, medium, or high uncertainty, but that is not an OCR confidence score or calibrated probability.

The offline nine-case baseline completed with zero extraction failures. Exact header matches are supplier 6/9, invoice number 8/9, invoice date 7/9, and PO reference 9/9. Printed subtotal and tax are each 8/9, printed total 9/9. The focused review matched 25 of 27 lines by identifier, description, then printed number; the moderately blurred C price case has no readable rows. Identifier accuracy is 19/27. Printed line-number accuracy is 3/9, because only supplier C prints line numbers; `source_row_order` is recorded separately. These are mock-provider contract results, not real LLM performance. The report counts null as missing and a nonmatching value as unsupported, and compares deliberately incorrect printed values with their printed ground truth, never corrected PO values. Supplier C is printed in uppercase, so its exact supplier-name match fails against title-case fixture truth; no casing is silently normalized. The original OCR and fixture files are unchanged. See [EXTRACTION_QUALITY_REVIEW.md](EXTRACTION_QUALITY_REVIEW.md) for matching, denominators, and provider test limits.

## Invoice-local validation baseline (Task 12)

Validate the nine stored Task 11 extraction objects against their corresponding OCR documents. This is an offline pipeline assessment, not a real LLM quality estimate. The validator receives no ground truth, expected finding, catalog, or PO data. `scripts/run_validation.py` prints each case's provisional decision and finding codes without modifying artifacts. Direct controlled tests exercise exact arithmetic and policy boundaries separately from the imperfect mock output. `fixture_a_arithmetic_02` must show `subtotal_mismatch` when its printed line totals and subtotal are extracted, and missing values must not count as passing arithmetic.

The categories are distinct: extraction failure means no usable extraction was produced; invoice-local validation failure means present observations violate local rules or required data is absent; external reconciliation remains **not evaluated**. The result records `external_reconciliation_evaluated=false`. A `ready_for_po_matching` result must never be counted as end-to-end clean, auto-approved, or posted. PO reference, quantity, price, and supplier discrepancies require purchase-order data. Unknown products require catalog data. No such findings are asserted by this stage. Review decisions on stored fixtures can reflect OCR/mock defects rather than seeded business defects.

The focused [validation quality review](VALIDATION_QUALITY_REVIEW.md) traces the clean A OCR quantity error, audits all nine default results, and compares decisions at OCR floors 0, 25, 50, 75, and 90.

Confidence assessment reports per-field presence, OCR evidence availability, minimum cited OCR word confidence, model-reported uncertainty, and numeric evidence support. OCR confidence is on Tesseract's 0–100 scale; model uncertainty is an uncalibrated label. The default OCR review floor is 50. No composite or statistical confidence percentage is calculated. Numeric evidence matching checks whether a cited numeric token contains the exact observed number; it does not prove the cited field is the correct label or row. Text and date citations are location evidence only.

Arithmetic uses exact `Decimal` comparisons with **zero tolerance** and no rounding. Comparisons requiring absent values are skipped while missing findings force review. Validation does not mutate printed values. Negative printed monetary values are now representable at extraction and flagged here; posting-domain values remain nonnegative. Neither this nine-case set nor its mock extractor establishes real-world rates.

## Task 13 reconciliation evaluation

`scripts/run_reconciliation.py` prints both modes for all nine cases. Controlled mode transcribes *invoice-visible* fields into synthetic high-confidence evidence and runs Task 12 plus Task 13 against an evaluation-only trusted snapshot. It is a rule check, not an OCR or LLM score. Stored mode reads existing Task 11 extraction and OCR JSON, runs Task 12 unchanged, and applies the same trusted snapshot. Neither mode writes output files or changes database state. Production reconciliation cannot import fixture ground truth through its repository protocol.

| Fixture | Controlled Task 13 findings; decision | Stored Task 13 findings; decision |
| --- | --- | --- |
| A clean 01 | none; ready | PO quantity mismatch from OCR `53`; review |
| A arithmetic 02 | none; review from Task 12 subtotal | 3 missing identifiers; review from Task 12 and Task 13 |
| A PO reference 03 | unknown PO reference; review | unknown PO reference; review |
| B clean 04 | none; ready | none; review from Task 12 low OCR confidence |
| B quantity 05 | PO quantity mismatch; review | PO quantity mismatch and 4 missing unit costs; review |
| B unknown 06 | unknown product; review | unknown product; review |
| C clean 07 | none; ready | none; review from Task 12 low OCR confidence |
| C price 08 | PO price mismatch; review | none evaluable because no lines; review from Task 12 |
| C supplier 09 | PO supplier mismatch; review | PO supplier mismatch and 3 missing identifiers; review |

Stored B quantity also has one low-confidence OCR field and four missing printed line totals in Task 12. Stored A arithmetic has a subtotal mismatch in Task 12, with missing quantity and unreadable/low-confidence fields. Stored A clean retains the Task 12 line-extension mismatch; Task 13's quantity finding is a consequence of that same OCR error. Controlled A arithmetic remains Task 12's responsibility. No case is approved or posted.

Fixture snapshots contain one PO per case, no prior receipts, and no persisted invoices. They cannot measure duplicate-detection rates or real partial-receipt behavior; focused tests cover those rules. A missing or ambiguous identifier prevents product/PO comparison. Missing quantities and prices yield `missing_match_input`, never fabricated numeric mismatches. This separation is essential when reading the stored results.

The quality review adds `unperformed_checks` to each result and evaluation row. Controlled clean cases have an empty list; controlled A PO-reference has skipped PO/line checks, while controlled A arithmetic has none in Task 13 and remains under Task 12 review. Stored C price has `invoice_lines` and `duplicate_identity` skipped because its extraction has no lines or invoice number. Stored missing identifiers and costs expose skipped line checks instead of appearing to pass. The nine-fixture findings and decisions remain unchanged.

## Task 14 controlled posting evaluation

`scripts/run_posting_evaluation.py` creates a temporary PostgreSQL database, upgrades Alembic, seeds controlled invoice-visible values and trusted purchasing rows, records explicit staff approval evidence, runs posting scenarios including competing invoices on one PO, then drops the database. It reuses test seeding code only inside the evaluation harness; the production posting module never imports fixture truth or test code. No original fixture, OCR, extraction, validation, or reconciliation output is modified.

| Scenario | Expected result |
| --- | --- |
| Clean invoice | First request posts two receipts; exact retry returns `already_posted` with no extra stock |
| Concurrent duplicate requests | One posts and one returns `already_posted`; two total receipt rows |
| Competing invoice beyond PO capacity | `insufficient_po_quantity`; invoice state, receipts, and balances unchanged |
| PO closed after approval | `po_ineligible`; invoice state, receipts, and balances unchanged |

This checks transaction behavior with controlled accurately transcribed values, not live OCR or LLM accuracy. The disposable database is the only state changed during evaluation. Stored OCR-imperfect fixtures are never automatically posted.

## Task 15 disposable HTTP demo

`docker compose run --rm test python scripts/run_api_demo.py` creates and drops a temporary PostgreSQL database and PDF storage directory. It uploads the existing fictional scanned A fixture through FastAPI, runs real OCR and the deterministic mock, retrieves the Task 12 line-extension finding, records a staff correction of the second quantity from OCR `53` to invoice-visible `3`, re-runs local and trusted checks, approves an explicit location and PO-line allocation, posts one receipt set, and verifies that the same-key retry returns `already_posted`. It checks two receipt movements, inventory balances of 2 and 3, and audit actions. The original PDF, OCR words, and extraction record remain unchanged; the corrected value is separate staff evidence. This demonstrates workflow wiring, not live model accuracy. No stored evaluation outputs or persistent application inventory are changed.

## Later full-dataset generation

- Use varied, synthetic UPC and NDC identifiers that satisfy the contract rules; avoid repeating the all-zero review identifiers across the corpus.
- Vary fictional products, quantities, invoice dates, unit prices, and line counts across suppliers and case categories while keeping arithmetic and ground truth consistent.
- Add explicit currency information once the generation and evaluation contracts support it. Until then, do not infer or print a currency in the PDF layouts.
- Consider fictional supplier addresses and fictional pharmacy details to make the corpus more realistic. Review every fixture to exclude real pharmacy data, real businesses, and proprietary AYC material.
