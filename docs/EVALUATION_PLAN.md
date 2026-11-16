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

## Later full-dataset generation

- Use varied, synthetic UPC and NDC identifiers that satisfy the contract rules; avoid repeating the all-zero review identifiers across the corpus.
- Vary fictional products, quantities, invoice dates, unit prices, and line counts across suppliers and case categories while keeping arithmetic and ground truth consistent.
- Add explicit currency information once the generation and evaluation contracts support it. Until then, do not infer or print a currency in the PDF layouts.
- Consider fictional supplier addresses and fictional pharmacy details to make the corpus more realistic. Review every fixture to exclude real pharmacy data, real businesses, and proprietary AYC material.
