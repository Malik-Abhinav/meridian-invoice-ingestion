# Task 10 OCR evaluation-quality review

The same nine Task 9 scans were evaluated with English Tesseract 5.5.0 at 200 DPI. No invoice or ground-truth fixture was changed. [Baseline report](../output/ocr/report.json) contains per-invoice timing, field misses, and errors; [benchmark results](../output/ocr/benchmark.json) contain all five configurations and their per-case scores.

## Metric definitions

The selectable original PDF supplies the full printed-text transcript, including headings and footers. Fixture ground truth supplies invoice-visible field values; expected corrected PO or arithmetic values are excluded from OCR recognition scoring. Both transcript strings use `normalize_text`: collapse all whitespace to one space and trim; preserve case, punctuation, and misrecognized characters. CER is character Levenshtein edits divided by reference characters. WER is ordered whitespace-token Levenshtein edits divided by reference words. Aggregate CER/WER divide **total edits by total reference length**, not the mean of invoice rates.

The supplemental **token multiset dissimilarity** ignores token order and preserves duplicates. For each exact, case-sensitive whitespace token, let `R` and `O` be its counts in the normalized reference and OCR text. `missing = Σ max(R−O, 0)` and `extra = Σ max(O−R, 0)`. The rate is `(missing+extra)/(reference token count+OCR token count)`. The aggregate uses sums of those counts across cases. A rate of zero means the token multisets match exactly, even if reading order is wrong. This is **not conventional WER**. The baseline aggregate rate is **0.0945**.

Field recognition counts invoice-visible values found anywhere in OCR text. Numeric values must match at boundaries that exclude digits and decimal points, so quantity `4` is not credited from price `4.0000`. It does not show whether a value was assigned to the right field or line. Repeated quantities can still be overcounted by presence matching. Full-transcript CER/WER also reflect differences between PDF text-stream order and Tesseract visual order.

## Uniform configuration benchmark

Run `docker compose run --rm test python scripts/benchmark_ocr.py` from the repository root. Each setting is applied to all nine scans; the original default remains grayscale, 200 DPI, PSM 3, and contrast 1.0.

| Configuration | CER | WER | Multiset dissimilarity | Field values present | Clean-case values present |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 0.402 | 0.436 | 0.094 | 125/144 | 47/48 |
| Contrast 1.5 | 0.378 | 0.420 | 0.071 | 131/144 | 47/48 |
| PSM 6 | 0.443 | 0.502 | 0.173 | 124/144 | 46/48 |
| PSM 11 | 0.391 | 0.467 | 0.126 | 120/144 | 46/48 |
| Contrast 1.5 with PSM 6 | 0.453 | 0.514 | 0.151 | 131/144 | 46/48 |

Contrast 1.5 helps the moderate-blur case and recovers two identifiers in the mild-blur case. Clean-case field presence does not fall, but some nonclean cases worsen and the sample is small. It remains an opt-in setting (`--contrast 1.5`), not a new default. PSM 11 improves the low-contrast compact case's sequence WER from 0.812 to 0.438, while its token multiset stays exact; aggregate field presence and WER worsen. No case-specific profile or identifier correction is hardcoded.

## Three diagnosed cases

| Case | Baseline CER / WER / multiset | Contrast 1.5 CER / WER / multiset | Recognition versus order |
| --- | --- | --- | --- |
| `fixture_b_quantity_05` | 0.802 / 0.812 / 0.000 | 0.802 / 0.812 / 0.000 | All 64 reference tokens appear with identical counts; Tesseract reads description/quantity columns before invoice metadata and price columns. This is an order failure, with no missing or extra tokens. |
| `fixture_c_price_08` | 0.713 / 0.797 / 0.667 | 0.447 / 0.594 / 0.442 | Genuine recognition loss: only 15 of 69 reference tokens remain at baseline. Contrast recovers line values but not all identifiers or the exact invoice number. |
| `fixture_a_arithmetic_02` | 0.264 / 0.283 / 0.181 | 0.324 / 0.350 / 0.034 | Genuine small-text, footer, and label omissions coexist with row-order differences. Contrast recovers two of three identifiers, while sequence CER/WER worsen. |

Exact baseline examples from the OCR JSON and text:

- `fixture_b_quantity_05`: reference and OCR token multisets are identical. The recognized sequence begins `Fable Ridge Distribution Qty Description / code 3 Imaginary amber...`; the reference places `INVOICE FICT_B-INV-0005 09 Feb 2026 PO FICT_B-PO-0005` before the table. The invoice number token is recognized exactly with confidence 89.0.
- `fixture_c_price_08`: expected printed `FICT_C-INV-0008`; OCR produced `FICT_C-4NV-0008` with confidence 30.0. Both printed identifiers `000000080156` and `00000080255` are absent. At baseline, `17.5000` and `9.3750` are also absent. Contrast 1.5 recovers those prices but produces `FICT_C-4INV-0008`, still incorrect.
- `fixture_a_arithmetic_02`: expected printed UPC `000000020121`, NDC `00000020247`, and UPC `000000020367` are all absent at baseline. Expected quantity `4` became `a` (confidence 41.0); `Line` became `ine` (confidence 89.0). Contrast 1.5 recovers the latter two identifiers but still misses `000000020121` and reads the quantity as `o`.

Remaining genuine OCR failures include the moderate-blur omissions, small identifiers on the mild-blur scan, and missing identifiers/date on the combined-degradation scan. The benchmark is a nine-case synthetic study, not evidence that one setting generalizes to real pharmacy invoices. No extraction, matching, or validation inference is made from field presence.
