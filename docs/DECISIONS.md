# Architectural decisions

## 1. Python package foundation

- **Decision:** Target Python 3.12 and use a `src/meridian` package with setuptools metadata in `pyproject.toml`.
- **Reason:** The `src` layout tests the installed package and avoids importing an uninstalled project directory by accident. Python 3.12 is the agreed project version.
- **Trade-off:** Contributors must install the package in editable mode before running tests or importing it.

## 2. Dependency scope

- **Decision:** Keep runtime dependencies empty. Put pytest in an optional `test` extra.
- **Reason:** This task only establishes the package and its test runner. Application and database libraries will be added when their features are implemented.

## 3. Incremental development

- **Decision:** Add one independently testable task at a time, updating this decision log when design choices become concrete.
- **Reason:** Each change can be verified and reviewed before the next task begins.

## 4. Invoice lifecycle contract

- **Decision:** Use string enums for states and operations, with an explicit transition table. Extraction, validation, human approval, and marking a successful posting are separate operations.
- **Reason:** An invoice cannot be approved before validation, posted before approval, or changed through ordinary correction after posting. Rejected and posted invoices are terminal in this initial contract.
- **Review path:** A correction to a review-required or validated invoice returns it to `extracted`, so validation must run again before approval. `review_required` cannot be approved directly. Extraction failures are terminal for the invoice; a later retry will be modeled as another upload attempt.
- **Posting boundary:** `MARK_POSTED` changes lifecycle state only. A later application service must persist that state in the same database transaction as inventory movements and balance changes. This module does not mutate stock.
- **Auto-approval:** No automatic approval transition is defined yet. A future task may add one after deterministic blocking rules and conservative thresholds are implemented.

## 5. Exact money and timestamps

- **Decision:** Domain money values must be finite, nonnegative `Decimal` instances. Float, integer, and string inputs are rejected. The domain does not round or choose a currency scale yet.
- **Reason:** Callers must parse money deliberately, preserving exact input precision. Database precision and currency policy will be defined with the schema.
- **Decision:** Timestamps must be timezone-aware `datetime` values and are normalized to UTC at the domain boundary.

## 6. Canonical invoice identity and upload attempts

- **Decision:** Store immutable document identity by SHA-256, record every upload attempt separately, and create/link a canonical invoice only after supplier identity is known. A partial unique index protects `(supplier_id, invoice_number_normalized)` when a number exists.
- **Reason:** Repeated bytes and conflicting documents remain traceable without creating duplicate canonical invoice obligations. Missing invoice numbers can remain unresolved.
- **Trade-off:** Hash matching, number normalization, and conflict classification need explicit service logic later. Database uniqueness cannot decide whether two different documents represent the same invoice.
- **Decision:** Use SQLAlchemy 2.x typed declarative models and PostgreSQL-compatible UUID, `NUMERIC(18,4)`, and timezone-aware timestamp columns. Only SQLAlchemy is added as a runtime dependency; a PostgreSQL driver waits until database execution is needed.
- **Trade-off:** Model metadata tests do not prove that PostgreSQL enforces constraints. Task 5 will verify the migration and live constraints.

## 7. Purchase orders, inventory, and audit schema

- **Decision:** Use `draft`, `open`, `closed`, and `cancelled` purchase-order statuses. Keep ordered quantities on PO lines and derive received quantities from receipt movements and their reversals.
- **Reason:** Partial receipts are visible without maintaining a second counter that can diverge from the movement ledger. A closed order need not equal its ordered quantity.
- **Decision:** Keep one nonnegative integer balance per product/location and an append-only movement ledger with `receipt`, `adjustment`, and `reversal` types. Unique posting keys, receipt invoice-line keys, and reversal targets support idempotent future operations.
- **Trade-off:** Schema constraints alone do not ensure atomic posting, safe concurrent balance updates, or immutable records. Those require a transaction service and PostgreSQL verification in later tasks.
- **Decision:** Store audit events separately from extraction data, with actor and entity identity plus optional JSONB before/after snapshots. Staff events require an actor ID; system events may omit one.
- **Decision:** Retain four decimal places for unit cost and monetary fields. Future input services must reject values with excess scale and apply explicit currency rounding rules; the database must not silently decide business rounding.

## 8. Local Docker foundation

- **Decision:** Use one PostgreSQL 16 service and one Python 3.12 test/development service on a private Compose network. Persist database files in a named volume, without publishing the database port to the host.
- **Reason:** The test container can reach the database by service name while keeping the local stack small and isolated. Compose waits for PostgreSQL's health check before starting the Python service.
- **Decision:** Install the project from `pyproject.toml` in the Python image with `test` and `db` extras. The optional `db` extra adds psycopg only where PostgreSQL access is needed.
- **Decision:** Put development-only credentials in an ignored `.env` file copied from `.env.example`; Compose constructs `DATABASE_URL` for the Python service. No credentials are embedded in application code.
- **Verification:** The Python Compose service ran all 80 tests and connected to PostgreSQL 16 with `SELECT 1`. A health check is a startup gate, not a guarantee that the database remains available for the lifetime of a command.

## 9. Initial Alembic migration and PostgreSQL verification

- **Decision:** Keep a reviewed initial revision with a fixed identifier (`0001_initial`) covering every model registered in `meridian.models`. Alembic reads `DATABASE_URL` at execution time, and no URL or credentials live in its configuration file.
- **Reason:** Fresh environments can create the same schema, and the revision can be checked against model metadata. Autogenerated revisions require manual review; the initial revision was checked for foreign keys, named checks, partial unique indexes, `NUMERIC(18,4)`, and timezone-aware columns.
- **Decision:** Live integration tests create uniquely named temporary PostgreSQL databases, exercise upgrade/downgrade/reupgrade and constraint failures, then drop only those test databases. The development database and its volume remain untouched.
- **Limitations:** Database constraints do not implement invoice matching, money input precision validation, immutable movement/audit rows, or atomic posting. Those remain application and transaction tasks.

## 10. GitHub Actions CI

- **Decision:** Run one read-only CI job for pushes to `main` and pull requests targeting `main`, with Python 3.12, a PostgreSQL 16 service, and dependencies installed from `pyproject.toml`.
- **Decision:** Apply migrations before tests, verify the full pytest result has no skips and includes all four PostgreSQL integration tests, then run `alembic check`.
- **Reason:** A missing database configuration must fail CI rather than quietly turn live constraint checks into skipped tests. The workflow uses temporary test-only credentials and pip caching; it has no deployment or write permission.

## 11. Synthetic case and ground-truth contracts

- **Decision:** Add Pydantic 2 as a runtime dependency for strict, frozen synthetic schemas. Keep `SyntheticInvoiceInput` and `ExpectedGroundTruth` as separate models inside a case. A renderer will receive only the input model. The schemas do not depend on database models, OCR, or PDF libraries.
- **Reason:** Evaluation annotations such as unknown products, PO quantities, reference prices, and duplicate links must not leak into generated documents. Validation catches inconsistent case plans before a future corpus is rendered.
- **Decision:** Require three distinct fictional suppliers and at least 60 planned cases spanning all seven primary categories. Use a versioned SHA-256-derived seed per case, avoiding process-randomized `hash()` and making case order irrelevant. This task does not generate those cases.
- **Decision:** Accept canonical UPC-A (12 ASCII digits with check digit) and 11-digit 5-4-2 NDC strings. Require at least one identifier per synthetic product. Treat these as format checks, not claims of registry validity or database identity.
- **Decision:** Require exact `Decimal` money with four or fewer fractional digits and 18 or fewer total digits; reject float and string inputs. Displayed totals may be absent only for missing-total cases; expected arithmetic totals remain in ground truth.
- **Limitations:** A boolean fictional marker cannot prove invented names or identifiers. Future fixture review must exclude real pharmacy data and proprietary materials. Task 6 did not include PDF rendering, image processing, OCR, a complete corpus, or scoring logic.

## 12. Three supplier PDF layouts

- **Decision:** Use ReportLab Platypus as the sole PDF runtime library and pypdf as a test-only reader. Tables and paragraphs provide wrapping, repeated column headings, and page breaks; no browser or image renderer is needed for text PDFs.
- **Decision:** Add an explicit `SupplierLayout` enum and an optional purchase-order reference to generation inputs, with a matching reference in ground truth. The three templates share exact numeric and identifier formatting but use different placement and column order: traditional header/table/bottom totals; compact side panel/early totals; purchase-order heading/grouped product tables/separate summary.
- **Decision:** Render money to four decimal places, matching the database's `NUMERIC(18,4)` scale, without inventing a currency or rounding to cents. Include only optional fields actually present. Escape invoice text before passing it to ReportLab markup.
- **Decision:** Set ReportLab's document invariant mode for reproducible PDF bytes. Render from `SyntheticInvoiceInput` only; evaluation annotations cannot be passed as renderer data. The `scan_blur` flag is ignored until scanned-image variants are implemented.
- **Verification:** Focused PDF tests check extracted text, optional fields, multi-page tables, and byte equality. Three one-page samples and multi-page stress documents were rendered and visually inspected for overlap and clipping.
- **Limitations:** Built-in Helvetica is suitable for the current ASCII synthetic fixtures; broader Unicode font coverage would require embedding an appropriate font. Layout samples are not a complete synthetic corpus.

## 13. Deterministic scanned-image variants

- **Decision:** Use pypdfium2 for PDF rasterization and Pillow for blur, contrast, rotation, and JPEG recompression. Reuse ReportLab to place processed images into image-only PDFs. Avoid OpenCV and external rasterizer executables for this task.
- **Decision:** Keep `rasterize_pdf`, `degrade_image`, and `scan_invoice` separate. Require the source PDF bytes to match the deterministic text renderer for the supplied `SyntheticInvoiceInput`, preserving source identity and keeping evaluation ground truth outside the scan boundary.
- **Decision:** Derive a case seed with Task 6's `derive_case_seed`, then derive each page seed using a versioned SHA-256 payload. Use the page seed for bounded rotation angles; record resolved blur, rotation, contrast, JPEG quality, pixel dimensions, DPI, and source/output hashes in separate JSON metadata.
- **Decision:** Rasterize review scans at 150 DPI. Rotate with canvas expansion and white fill so no invoice content is cropped. The output PDF contains only image draw operations and no selectable page text; the original PDF stays unchanged.
- **Verification:** Tests cover seven profiles, deterministic PDF and pixel output, metadata, page count, source matching, and ground-truth isolation. Seven actual samples across all three suppliers were visually reviewed for readability and clipping.
- **Limitations:** Settings are controlled examples rather than a calibrated real scanner model. PDFium and Pillow version changes may affect pixels despite stable seeds; pin versions for any future byte-for-byte archival corpus. The full 60-case dataset and OCR remain separate tasks.

## 14. Nine evaluation fixtures

- **Decision:** Keep nine fixed case specifications in source, three per fictional supplier. Each supplier has one clean case, one data or arithmetic discrepancy, and one identity or matching discrepancy. Use the existing PDF layouts and scan profiles with master seed 2029 and 150 DPI. Do not modify the approved Task 7 and Task 8 samples.
- **Decision:** Preserve `InvoiceTotals` for internally consistent invoice inputs. Introduce an explicit `PrintedTotals` type for deliberately inconsistent invoice-visible totals; the expected correct totals remain in separate fixture ground truth. Python money inputs still require `Decimal`. JSON parsing accepts decimal strings so generated ground-truth files can be read back exactly, but JSON numeric money inputs remain invalid.
- **Decision:** Keep expected PO reference, supplier, line quantities and costs, known SKUs and printed identifiers, seeded discrepancies, expected findings, and processing decision in evaluation-only JSON. PDF and scan functions receive only `SyntheticInvoiceInput`. A relative-path manifest includes empty `related_case_ids` for later duplicate and idempotency scenarios.
- **Decision:** Label clean fixtures `auto_post_candidate`, not approved or posted. Any seeded discrepancy is `review_required`. These labels define evaluation expectations, not a validation engine or posting policy.
- **Trade-off:** PO data are small ground-truth records rather than persisted purchase orders. Fixture tests independently compare those records with invoice values but do not establish real reconciliation, partial-receipt, or posting behavior. Version changes in ReportLab, PDFium, or Pillow can alter output bytes despite fixed seeds; reproducibility is verified with the current project environment.

## 15. Tesseract OCR observations and evaluation

- **Decision:** Add pytesseract as the only OCR Python dependency and install Tesseract with English data in Docker and CI. Reuse pypdfium2 for page rasterization. Run OCR page by page at 200 DPI with PSM 3 and record Tesseract version, configuration, dimensions, confidence, boxes, and reading-order IDs.
- **Decision:** Use grayscale by default; keep contrast enhancement, median denoising, thresholding, and projection-based small-angle deskewing opt-in. Preserve original scans and ground truth. Treat no recognized words as an explicit empty-scan error and report invalid PDFs and engine failures distinctly.
- **Decision:** Keep original word observations in JSON and form plain text only by joining Tesseract lines and collapsing whitespace. Do not correct OCR spelling or invent missing invoice fields.
- **Decision:** Use printed selectable PDF text as the complete CER/WER transcription reference and fixture ground truth as the field-value reference. Compare displayed invoice values, including seeded mistakes, rather than expected corrected PO values. Record per-case processing time, field hits/misses, and failures.
- **Limitations:** PDF text stream order and OCR reading order can differ substantially, inflating sequence CER/WER. Exact-value presence does not prove correct field or line association and can overcount repeated quantities. OCR quality depends on Tesseract version and scan profile; moderate blur remains difficult. No LLM extraction or business validation is implemented.

## 16. OCR evaluation-quality review

- **Decision:** Retain reading-order-sensitive CER/WER and their micro-aggregate calculation. Normalize both reference and OCR text by collapsing whitespace only. Add token multiset dissimilarity with exact, case-sensitive tokens and duplicate counts; divide total missing plus extra tokens by total reference plus OCR tokens. Never label this companion metric WER.
- **Decision:** Benchmark baseline, contrast 1.5, PSM 6, PSM 11, and contrast 1.5 with PSM 6 across the same nine scans. Keep the grayscale/PSM 3 default unchanged. Contrast 1.5 improves aggregate metrics and retains clean-case field presence, but some individual nonclean cases worsen; opt-in use avoids treating nine synthetic invoices as a universal tuning set.
- **Reason:** The low-contrast compact scan has the correct token multiset despite a high sequence WER; moderate blur genuinely drops most tokens. Reporting both kinds of error prevents layout order from being mistaken for recognition loss.
- **Correction:** Exact-value presence excludes numeric substrings of decimal values; a quantity `4` cannot be credited from a price `4.0000`. This affects the PSM 11 benchmark field count, while transcript metrics and OCR defaults remain unchanged.

## 17. Structured extraction boundary

- **Decision:** Define separate Pydantic extraction contracts so incomplete, duplicated, and contradictory observations remain representable. Store each field's observed value or null, model uncertainty, and OCR page/word/line references. Validate references against the supplied OCR document; keep OCR confidence in that source document. This establishes schema validity, not factual or business-rule validity.
- **Decision:** Accept only finite, nonnegative decimal-string or `Decimal` money with at most four fractional places and 18 digits. Reject floats and JSON numbers; never calculate or repair invoice values during extraction.
- **Decision:** Use a deterministic OCR-only mock for CI and an optional OpenAI Responses provider through standard-library HTTPS, `OPENAI_API_KEY`, configurable model and timeout, strict JSON schema, and `store: false`. Keep invoice text in a data message under fixed extraction instructions. No external call occurs in offline tests; live provider behavior and model quality remain unverified.
- **Decision:** Retry only transient provider failures (transport, HTTP 429, or server errors) at most three times and make at most one explicit repair request for malformed JSON or schema/evidence failures. Authentication and malformed API responses fail directly. A repair can only retranscribe OCR-supported values; no ground truth enters provider input.
- **Decision:** Evaluate exact invoice-visible headers, printed monetary values, product identifiers, and matched line fields. Do not use expected PO values or corrected arithmetic as extraction targets. The offline mock is intentionally conservative and leaves ambiguous rows null; its scores do not estimate LLM quality.
- **Limitation:** The Docker Compose network is intentionally internal; the optional live provider must run from a separate network-enabled local environment. The API integration has only simulated HTTP tests, so live connectivity, response quality, and prompt-injection resistance are not proven by CI.

## 18. Extraction quality review

- **Decision:** Separate `source_row_order` from printed `line_number`. The former identifies each extracted OCR row; the latter remains null when no printed number is readable. Zero or negative printed integer values remain representable for later business-rule review. Null unreadable values may cite real OCR words, while nonexistent page, word, or line references are rejected.
- **Decision:** Score printed line numbers only where the supplier layout actually prints them. Match extracted rows by exact identifier, then description, then printed number, and report unmatched/extra rows explicitly. This corrects the line-number denominator from 27 to 9 and avoids a wrong number taking precedence over matching product evidence.
- **Decision:** Recheck the OpenAI Responses request against the current official strict JSON schema contract. Simulated HTTP tests cover transient retry, fatal authentication/client errors, malformed responses, refusal, request-body identity, and absence of credentials from payloads and error text. No live call was made because no key was available, and the test network is internal.
- **Limitation:** Valid OCR evidence references establish locations, not factual support. The deterministic mock's layout heuristics and null uncertainty do not measure LLM performance. See `EXTRACTION_QUALITY_REVIEW.md` for results and remaining OCR failures.

## 19. Invoice-local validation and review routing

- **Decision:** Make validation a pure function of the extracted invoice, OCR document, and explicit policy. Findings use stable codes, severity, field/row references, observed values, and available OCR references. No source value is repaired or overwritten. Purchase-order, catalog, supplier identity, duplicate-invoice, and inventory checks remain later stages.
- **Decision:** Accept signed printed monetary observations in the extraction contract so a negative printed amount is preserved and diagnosed. This does not change nonnegative domain money, generated fixtures, SQL constraints, or stock eligibility. Continue rejecting floats, JSON numeric money, nonfinite values, excess scale, and amounts exceeding 18 digits.
- **Decision:** Use exact `Decimal` arithmetic with zero tolerance at up to four fractional places. A missing operand makes an arithmetic check not evaluable; a separate missing-value finding requires review. No currency rounding rule is inferred.
- **Decision:** Set `ready_for_po_matching` only when there are no findings requiring manual review. Current warnings and errors require review; informational findings can be nonblocking. The optional `reject_empty_invoice` policy alone permits `rejected` for an extraction with no lines; default behavior sends it to review. These are provisional recommendations and do not change lifecycle state or authorize inventory posting.
- **Decision:** Keep field presence, OCR evidence, minimum cited OCR confidence, provider uncertainty, numeric citation support, and rule severity separately accessible. Default OCR floor is 50; values below it require review. There is no composite score or probability, because nine synthetic mock cases cannot calibrate one. Citation location alone does not prove semantic support; numeric token mismatch is a narrow conflict check, while text/date semantics remain unverified.
- **Limitation:** An exact duplicate identifier can represent a legitimate repeated product line, so it is a review warning rather than automatic rejection. OCR confidence can be low on a true value. The offline mock may produce a local arithmetic inconsistency on a seeded clean case; a case with a seeded PO discrepancy may pass this local stage. Neither outcome establishes end-to-end correctness.

## 20. Validation quality review

- **Finding:** The clean A fixture's second row prints quantity `3`, but OCR read `53`; Task 11's mock retained that value in the correct row. Task 12's `397.5000` versus `22.5000` extension mismatch is correct and remains a review finding.
- **Decision:** Verify every cited OCR page, word index, and line ID in validation. When supplied an `ExtractionResult`, require its SHA-256 to match the OCR document. A bare invoice cannot prove document identity. A whole-row citation with several distinct numbers is not semantic support for a particular numeric column; mark it not evaluable instead of supported.
- **Decision:** Keep the configurable default OCR floor of 50 as an explicitly uncalibrated, conservative heuristic. Take the minimum confidence across all cited words and references. Low OCR confidence on noncritical descriptions does not block; missing descriptions do. Document the nine-fixture threshold sensitivity in `VALIDATION_QUALITY_REVIEW.md` without tuning the floor to these cases.
- **Decision:** Only findings whose `manual_review_required` flag is true block progression. Current warnings and errors require review; informational findings can be nonblocking. The explicit empty-invoice rejection policy is unchanged.
