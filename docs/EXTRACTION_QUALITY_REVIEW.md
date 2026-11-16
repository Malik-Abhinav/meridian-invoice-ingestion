# Task 11 extraction quality review

The nine-case offline run uses only the stored Task 10 OCR observations. It makes no external LLM call. Ground truth is loaded after extraction for evaluation, and original PDFs, corrected PO values, expected findings, and processing decisions never enter provider input. The mock is a deterministic contract test, **not a measure of real LLM performance**.

## Line numbering and matching

The original 3/27 line-number figure used every generated product line as a denominator. Suppliers A and B print quantities but no line numbers: 18 of the 27 lines have no printed number to extract. Supplier C prints numbers in two identifier groups, with NDC rows ahead of UPC rows, so printed order can be `2, 4, 1, 3`. OCR recognizes three of its nine printed line numbers: three on the clean C invoice, none on its moderate-blur invoice, and none on its combined-degradation invoice. The applicable exact-match result is **3/9**, with six missing; the other 18 are not applicable.

`source_row_order` now records the order in which the provider found rows in OCR, independent of `line_number`. It is positive and unique within an extraction. A printed number stays null when OCR does not support it; the mock does not infer `4` from a gap in `2, ?, 1, 3`. Evaluation matches rows by exact printed identifier first, exact description next, and printed number last. It reports unmatched ground-truth line numbers and extra extracted source-row orders. A wrong printed number can therefore be scored as wrong without causing a correct product row to be assigned to another product.

## Offline mock assumptions

The mock reads only the serialized `OcrDocument` page and word observations. It uses layout header phrases to select column order, reads visible dates, numbers and money without arithmetic repair, associates a UPC/NDC line with the preceding parsed product row, and leaves OCR-separated compact-layout prices null rather than joining distant columns. For supplier C it preserves product-group reading order. OCR errors such as `53` for quantity `3` remain errors; product identifiers absent from a catalog remain as printed. The mock returns no model uncertainty because it is a deterministic parser. It does not inspect the fixture manifest, ground truth, original PDFs, or expected outcomes.

## Evidence and provider boundaries

Every non-null field needs a page and word/line reference. A null unreadable field may cite the garbled OCR words that led to uncertainty; an unknown field may have no evidence. Page numbers, zero-based page word indexes, and Tesseract line IDs are checked against the supplied OCR document. Repeated words have distinct indexes. A valid reference means the cited OCR location exists; it does **not** prove the value matches those words. OCR confidence remains in the OCR JSON; model uncertainty is separate.

The optional OpenAI provider uses the Responses `text.format` JSON schema with strict mode, required properties and `additionalProperties: false`, consistent with the [current Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses). Tests simulate successful output, refusal, malformed responses, authentication errors, HTTP 429/5xx, and a transient retry. Identical request bodies on retry are verified. API keys remain in request headers, never request JSON or output files. Authentication and malformed-response failures are not retried; malformed extracted data receives at most one explicit repair request and sets `repair_attempted`. No live call was made because `OPENAI_API_KEY` was unavailable; the Docker Compose network is internal in any case. Live model accuracy, token usage, latency, cost, and prompt-injection resistance remain untested.

## Offline evaluation

| Field | Exact matches / applicable values |
| --- | ---: |
| Supplier name | 6/9 |
| Invoice number | 8/9 |
| Invoice date | 7/9 |
| PO reference | 9/9 |
| Printed subtotal / tax / total | 8/9 · 8/9 · 9/9 |
| Printed line number | 3/9 |
| Description / product identifier | 24/27 · 19/27 |
| Quantity / unit cost / printed line total | 23/27 · 21/27 · 21/27 |

Twenty-five of 27 product lines are matched, with two unmatched rows on `fixture_c_price_08` and zero extra extracted rows. The clean C invoice's OCR reads the printed `4` as `a`; its product identifier still allows the row to match while the printed number remains null. The arithmetic-discrepancy invoice retains its incorrect printed subtotal and total exactly, and the unknown-product invoice retains its printed NDC. Missing values never count as correct. Supplier C's uppercase printed name differs from the fixture's title-case name, so exact supplier-name scoring remains 6/9. The report at `output/extraction/report.json` contains per-invoice denominators and unmatched row IDs.
