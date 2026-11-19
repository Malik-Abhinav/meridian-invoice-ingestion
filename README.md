# Meridian Supplier Invoice Ingestion

[![CI](https://github.com/Malik-Abhinav/meridian-invoice-ingestion/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Malik-Abhinav/meridian-invoice-ingestion/actions/workflows/ci.yml)

An incremental, synthetic-data-only supplier invoice ingestion project. The implementation includes OCR, structured extraction, invoice-local validation, trusted purchasing reconciliation, atomic inventory posting, a FastAPI staff workflow, and a React staff review dashboard. All posting requires explicit staff approval.

## Staff review dashboard (Task 16)

The [frontend](frontend/README.md) is a separate React/TypeScript Vite application. With the [FastAPI service](docs/API.md) running locally, use Node.js 20.19 or later and run `cd frontend && npm ci && npm run dev`. Enter the configured development staff bearer token on the sign-in screen. The dashboard covers upload, processing, evidence inspection, correction, reconciliation review, explicit approval, separate posting, audit, and receipt movements. Its token is held in memory only. Frontend setup, browser verification, and known limitations are in the [frontend guide](frontend/README.md).

## Setup

Use Python 3.12:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

Run the tests and check the installed package:

```sh
python -m pytest
python -c 'import meridian; print(meridian.__file__)'
```

The editable install makes the `src/meridian` package importable while developing. The `test` extra installs pytest. SQLAlchemy is the current runtime dependency; database driver installation is optional for local package-only tests.

## Docker development environment

Docker Compose provides PostgreSQL 16 and a Python 3.12 test container. The database is reachable only on the private Compose network. Copy the development-only example settings before running Compose:

```sh
cp .env.example .env
# Set a local-only POSTGRES_PASSWORD in .env before continuing.
docker compose build
docker compose up -d db
docker compose run --rm test
```

The `test` service waits for the database health check before it starts. Its `DATABASE_URL` is assembled from the `.env` values and points to the `db` service. The Python image installs the project with its `test` and `db` extras; the latter supplies the PostgreSQL driver for future database tests.

To verify connectivity from the Python container:

```sh
docker compose run --rm test python -c 'import os; from sqlalchemy import create_engine, text; connection = create_engine(os.environ["DATABASE_URL"]).connect(); print(connection.execute(text("SELECT 1")).scalar_one()); connection.close()'
```

Stop the services while retaining local database data:

```sh
docker compose down
```

Reset the **local** PostgreSQL volume when you want a fresh database (this deletes its data):

```sh
docker compose down -v
```

The test suite includes model checks and live PostgreSQL constraint tests.

## Database migrations and live constraint tests

Alembic reads `DATABASE_URL` from the environment and includes every model registered in `meridian.models`. With the Compose database running, apply the initial revision to the local development database:

```sh
docker compose run --rm test alembic upgrade head
docker compose run --rm test alembic current
```

Run the full suite, including live PostgreSQL checks:

```sh
docker compose run --rm test
```

The integration tests create uniquely named temporary databases, run upgrade/downgrade/reupgrade and insert checks, then drop only those temporary databases. They do not reset the development database. The configured database user needs permission to create databases for these tests. To run only package and model tests locally without a PostgreSQL connection:

```sh
python -m pytest -m 'not postgres'
```

Use `alembic downgrade base` only on a disposable database; it drops the application tables in that database. The migration cycle test exercises this safely in its own temporary database.

## Continuous integration

The [CI workflow](.github/workflows/ci.yml) runs on pushes to `main` and pull requests targeting `main`. It uses Python 3.12 and a temporary PostgreSQL 16 service, applies migrations, runs the full pytest suite, fails if the PostgreSQL integration tests are missing or skipped, and checks for migration drift. No production database credentials are used.

## Synthetic invoice PDF layouts

Task 7 adds selectable-text A4 invoices for three fictional supplier styles. ReportLab is the only PDF runtime dependency; pypdf is installed with the `test` extra to inspect structure and extracted text in tests. The renderer accepts `SyntheticInvoiceInput` only, so evaluation annotations remain outside the PDF input.

Generate the three representative review samples from the project root:

```sh
python scripts/generate_pdf_samples.py
```

The outputs are [traditional](output/pdf/supplier_a_traditional.pdf), [compact](output/pdf/supplier_b_compact.pdf), and [purchase-order-oriented](output/pdf/supplier_c_purchase_order.pdf). They are fictional examples, not the planned 60-invoice dataset. Re-running the command produces identical PDF bytes for the same inputs. The `scan_blur` input flag is intentionally unused by this text renderer; the scan generator below selects an explicit degradation profile.

Run the focused PDF tests with `python -m pytest tests/test_pdf_invoices.py -q`, or the complete suite with `docker compose run --rm test` after rebuilding the image for the new dependencies.

## Scanned-image invoice variants

Task 8 uses pypdfium2 to rasterize the approved text PDFs and Pillow to apply seven controlled profiles: clean raster, mild blur, moderate blur, small rotation, lower contrast, JPEG artifacts, and a combined profile. ReportLab assembles the resulting images into image-only PDFs. Each scan has a JSON sidecar recording the source case and invoice, supplier layout, profile, resolved page parameters, seed, DPI, dimensions, and source/output hashes. The original text PDFs are read but never modified; evaluation ground truth is not passed to the scan generator.

Generate the seven review scans and sidecars from the three existing supplier PDFs:

```sh
python scripts/generate_scan_samples.py
```

The review files are under [output/scans](output/scans/). The script uses a fixed master seed and 150 DPI. It verifies that each source PDF matches the invoice input before rasterizing. These are samples, not the planned 60-invoice corpus. Run focused checks with `python -m pytest tests/test_scanned_invoices.py -q`, or the complete Docker suite with `docker compose run --rm test` after rebuilding the image for the new dependencies.

## Nine evaluation fixtures

Task 9 adds exactly nine fictional cases, three per supplier: one clean, one data or arithmetic discrepancy, and one identity or matching discrepancy. Generate the originals, image-only scans, ground truth, scan metadata, and manifest with:

```sh
python scripts/generate_evaluation_fixtures.py
python -m pytest tests/test_evaluation_fixtures.py -q
```

The [manifest](output/evaluation/manifest.json) maps each case ID to its files in [output/evaluation](output/evaluation/), records the degradation profile, and lists expected findings and a processing decision. Run the command from any directory; it always writes to the repository's `output/evaluation/`. The fixed seed is 2029 and resolution is 150 DPI. Repeating generation in the same environment produces byte-identical files. Ground truth contains expected PO and catalog values but is never passed to the PDF or scan renderer. Clean cases are only `auto_post_candidate`; fixture generation does not approve or post them. This is a small evaluation fixture set, not the planned 60-invoice corpus.

## Tesseract OCR observations

The Docker test image installs Tesseract and English language data. The OCR pipeline rasterizes each scanned PDF page, runs Tesseract with English and automatic page segmentation, and writes words with confidence, bounding boxes, Tesseract reading-order IDs, page dimensions, processing settings, and engine version. Only grayscale conversion is enabled by default. Contrast, denoising, thresholding, and small-angle deskewing are opt-in.

Process one scan or all nine fixtures inside Docker:

```sh
docker compose build test
docker compose run --rm test python scripts/run_ocr.py --input output/evaluation/scanned/fixture_a_clean_01.pdf --output-dir output/ocr/single
docker compose run --rm test python scripts/run_ocr.py --fixtures output/evaluation/manifest.json --output-dir output/ocr
```

Options include `--dpi`, `--contrast`, `--denoise`, `--threshold`, `--deskew`, `--no-grayscale`, and `--psm`. Each case produces `.ocr.json`, `.txt`, and `.evaluation.json`; [report.json](output/ocr/report.json) contains aggregate metrics and recorded failures. OCR output is separate from ground truth. JSON includes a whitespace-normalized transcript; the readable `.txt` keeps Tesseract's line breaks. Neither repairs recognition errors. The fixture command exits nonzero if any case fails.

CER and WER compare OCR text with the selectable text of the original PDF, including printed labels and footers. This measures transcript fidelity but is sensitive to reading order and does not use expected corrected PO or arithmetic values. Key-field recognition checks the *displayed* invoice values from fixture ground truth: invoice number, supplier, date, PO reference, product identifiers, quantities, unit prices, and printed totals. It measures whether each exact value occurs in OCR text, not whether a future extractor assigns it to the correct field or line. Reported processing time includes OCR and evaluation file I/O. Run focused tests with `docker compose run --rm test python -m pytest tests/test_ocr.py tests/test_ocr_evaluation.py tests/test_ocr_cli.py -q`.

The report also includes token multiset dissimilarity, an order-insensitive companion to CER/WER. It counts duplicate tokens, missing tokens, and extra tokens; it is **not WER**. Reproduce the five-configuration comparison with `docker compose run --rm test python scripts/benchmark_ocr.py`. Results are in [benchmark.json](output/ocr/benchmark.json), with method and failure analysis in [OCR evaluation review](docs/OCR_EVALUATION_REVIEW.md). The original grayscale/PSM 3 default remains the baseline; contrast 1.5 is an opt-in setting to try on difficult scans.

## Structured invoice extraction (Task 11)

Extraction reads Task 10 OCR JSON, never fixture ground truth. The default `mock` provider is deterministic, offline, and conservative: it returns null for cells it cannot support from OCR. It is a contract and test provider, not an LLM quality estimate. A real OpenAI Responses provider is optional and uses `OPENAI_API_KEY` from the environment; `--model` or `OPENAI_MODEL` selects a model (default `gpt-4o-mini`). It sends OCR text, line groups, word indexes, boxes, and confidence to the provider with strict JSON schema output. It requests no web tools and sets `store: false`. The API integration has been tested with a simulated HTTP response, not a live paid call. See the [official Responses Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).

```sh
docker compose run --rm test python scripts/run_extraction.py --input output/ocr/fixture_a_clean_01.ocr.json --output output/extraction/one.json
docker compose run --rm test python scripts/run_extraction.py --fixtures output/evaluation/manifest.json --output-dir output/extraction
# Optional live call from a local Python environment with network access:
# OPENAI_API_KEY=... python scripts/run_extraction.py --provider openai --input output/ocr/fixture_a_clean_01.ocr.json --output output/extraction/live.json
```

Each value holds its observed value or null, model-reported uncertainty, and references to page and OCR word/line IDs. OCR confidence remains in the source OCR JSON and is distinct from model uncertainty. Decimal money is accepted only from strings or `Decimal`, with no rounding; schema validity is checked locally. No business-rule validity, PO matching, or approval decision is inferred. Malformed responses get at most one explicit repair attempt; only transient provider failures have bounded retries. `output/extraction/report.json` scores exact header, money, identifier, and line fields against invoice-visible ground truth only. The report is evaluation-only and ground truth never reaches a provider.

Each extracted line also has `source_row_order`, which records OCR reading order separately from any printed `line_number`. Missing printed line numbers stay null. Unreadable null values may cite OCR words; a valid reference identifies an OCR location but does not prove semantic correctness. The [extraction quality review](docs/EXTRACTION_QUALITY_REVIEW.md) explains the corrected printed-line-number denominator, mock assumptions, provider tests, and per-field results.

The Docker Compose network is internal, so live OpenAI requests require a separate, network-enabled local environment. Do not put an API key in `.env` or commit one to the repository.

## Invoice-local validation (Task 12)

`meridian.validation.validate_invoice(extracted_invoice, ocr_document, policy)` returns frozen Pydantic findings, field assessments, and a provisional decision. It reads only extraction and cited OCR observations. It never changes either input or reads fixture truth, catalog records, purchase orders, database state, or API credentials. Run the stored nine-case evaluation with:

```sh
docker compose run --rm test python scripts/run_validation.py
docker compose run --rm test python scripts/run_validation.py --ocr-floor 25
docker compose run --rm test python -m pytest tests/test_validation.py -q
```

Required headers are supplier name, invoice number/date, and PO reference. Every line needs an identifier (UPC or NDC), positive quantity, unit cost, and printed line total; missing descriptions are warnings. Repeated exact identifiers, uncertain or unreadable values, low-confidence OCR words, absent or contradictory numeric evidence, and zero/negative values receive explicit findings. Arithmetic compares quantity × unit cost with printed extension, the sum of printed extensions with subtotal when every line total is present, and subtotal + tax with grand total. Comparisons use exact `Decimal` values at the extraction contract's maximum four fractional places. **Tolerance is zero**; no rounding or correction occurs. A check without all required observations is skipped and missing-value findings remain visible.

Default policy requires review for every current error or warning. A future informational finding can be nonblocking. `ready_for_po_matching` means only that this invoice-local stage found no blocking issue; it does not approve the invoice or establish PO, catalog, supplier, duplicate, or stock eligibility. `rejected` is available only through the explicit `reject_empty_invoice` policy. Default minimum cited OCR word confidence is 50, and zero unit cost is prohibited unless policy permits it. Field assessments expose value presence, evidence availability, minimum cited OCR confidence, provider uncertainty, numeric evidence support, and review status separately. There is no composite score or calibrated accuracy probability. OCR confidence is a Tesseract signal; provider uncertainty is a different, uncalibrated signal. Text/date evidence locations are not semantically verified by this stage. Pass the full `ExtractionResult` to check its source hash against OCR; bare invoice input is intended for controlled validation.

Extraction now retains signed printed monetary observations (still exact decimal strings or `Decimal` and at most four fractional places) so validation can flag negative amounts. Domain money and database posting constraints remain nonnegative. Stored Task 11 extraction files and the nine original fixtures are unchanged. The stored offline mock has known OCR errors; a clean fixture may require review, and a seeded PO/catalog discrepancy may pass invoice-local checks. See [evaluation plan](docs/EVALUATION_PLAN.md) and [decision log](docs/DECISIONS.md).

The [validation quality review](docs/VALIDATION_QUALITY_REVIEW.md) traces the clean A fixture's OCR quantity error, records threshold sensitivity on all nine fixtures, and lists evidence limitations.

## Trusted purchasing reconciliation (Task 13)

`reconcile_invoice(extraction, validation, repository, policy)` compares an invoice with read-only trusted supplier, product, PO, receipt, and prior-invoice records. It preserves the Task 12 decision and findings separately. A `ready_for_posting_review` result requires Task 12 `ready_for_po_matching` and no Task 13 findings; it is **never** approval or inventory posting. Warnings and errors require review. Rejection requires explicit `ReconciliationPolicy.reject_codes`. Neither reconciliation nor the evaluation script writes to a database.

The [in-memory snapshot](src/meridian/reconciliation.py) supports deterministic tests. The [SQLAlchemy adapter](src/meridian/reconciliation_repository.py) reads existing tables and computes net PO receipts from receipt and reversal movements; supplier aliases are passed explicitly because the current schema has no alias table. It does not create another persistence model. Supplier names and aliases match after trimming, case folding, and collapsing whitespace. UPC-A requires 12 digits and a valid check digit. NDC requires an already normalized 11-digit 5-4-2 value; no missing digit is invented. Leading zeros are retained. PO products match by trusted catalog IDs, not invoice row numbers. Repeated invoice rows aggregate against remaining ordered quantity, which permits partial receipts. Unit costs compare exactly as `Decimal` values.

Duplicate identity uses supplier ID and invoice number after outer whitespace trim and ASCII case folding; punctuation and inner spaces remain significant. Source SHA-256 checks use persisted upload attempts. Exact repeats, changed documents with the same identity, and same documents under another identity receive distinct findings, including when one invoice has multiple uploaded hashes. Prior `failed` and `rejected` invoices are ignored by the default duplicate policy; `ignored_prior_statuses` is configurable. Callers reconciling an already persisted invoice or upload must pass its current ID so its own record is excluded. This read-only check is advisory; Task 14 must enforce transactional idempotency and uniqueness at posting.

Receipt availability is ordered quantity minus posted receipt movements plus their exact reversals. Unposted, failed, or rejected invoice movements do not count as receipts and mark the history incomplete; an invalid reversal also prevents a ready decision. Unrelated negative adjustments do not change PO receipt counts. `unperformed_checks` lists comparisons skipped because a supplier, PO, product, quantity, price, or receipt history could not be resolved. A ready decision requires this list to be empty. The invoice extraction contract has no trusted PO-line ID, so a product on multiple PO lines remains ambiguous even if printed line numbers happen to match. Human review needs a verified PO-line allocation and receipt history before reconciliation can continue. Concurrent receipt/posting races remain for Task 14's transaction boundary.

Run both nine-fixture modes and focused tests:

```sh
docker compose run --rm test python scripts/run_reconciliation.py
docker compose run --rm test python -m pytest -q tests/test_reconciliation.py
```

The **controlled** mode uses accurately transcribed invoice-visible values and synthetic evidence, then builds trusted reference snapshots from fixture ground truth outside the production path. It tests reconciliation rules, not OCR or model accuracy. The **stored** mode uses the unchanged Task 11 mock extractions and Task 12 validation, exposing OCR errors and skipped checks when values are missing. See [evaluation plan](docs/EVALUATION_PLAN.md) for per-case results. The current records do not model credit memos, supplier-specific identifier formats, negotiated price tolerances, or PO line allocation when the same product occurs on multiple PO lines; those situations require review.

## Atomic inventory posting (Task 14)

`inventory_posting.py` owns a single PostgreSQL transaction for an approved invoice. Before any stock change, a trusted staff workflow must persist an `AuditEvent` with `action="approve"`, the exact evidence from `prepare_posting_approval` including the target location, and an `approved` invoice state. `record_posting_authorization` re-runs Task 12 validation and Task 13 reconciliation against the linked source document and live trusted records, checks persisted invoice fields and explicit invoice-line-to-PO-line allocations, verifies the staff event, then stores one authorization row. The caller owns that authorization transaction. Neither preparation nor authorization posts inventory or silently creates a staff approval.

The new `invoice_posting_authorizations` table is the minimal durable boundary for approval evidence and invoice-level request identity. It stores the approved location ID and source, invoice, trusted supplier/catalog/PO, allocation, and decision fingerprints; one row per invoice; one approval event; and, after posting, a unique idempotency key, request fingerprint, and timestamp. The migration is `0002_posting_authorization`; the original supplier-invoice identity and receipt uniqueness indexes remain intact. A replay with the same completed key and request returns `already_posted` and the original movement IDs. A changed request, target location, or another key cannot post the invoice again. The staff actor must be authenticated by the upstream approval workflow; this repository does not yet provide that UI or identity service.

`post_invoice` takes only invoice, location, authorization, idempotency key, and explicit line allocation IDs. Product, quantity, and unit cost come from locked database rows. Under PostgreSQL `READ COMMITTED`, it locks the invoice, authorization, PO headers, and every line on each referenced PO; takes shared locks on referenced supplier and catalog rows; takes transaction-scoped advisory locks for normalized invoice identity and document hash; rechecks duplicates and posted receipt history; and checks remaining quantity after exact reversals. The approval fingerprint covers those trusted rows and all referenced PO lines, so changes to them make approval stale. The sorted PO locks serialize competing receipts. Receipt rows use stable posting keys and the existing unique invoice-line receipt index. Balances use atomic PostgreSQL upserts on the unique product/location pair, preventing lost increments. The invoice transition to `posted`, receipt movements, balances, and completed idempotency record commit together or roll back together; success is returned only after commit. Deadlocks and serialization failures return a typed transaction conflict for a bounded caller retry with the same key. No external OCR, model, network, or file operation occurs under posting locks.

The approval evidence records any explicit human resolution of an ambiguous PO-line allocation or repeated-product warning. Other Task 12 review findings, unresolved Task 13 findings, and unperformed checks cannot be authorized. Posting rechecks supplier, PO status, product, exact `Decimal` price, receipt limits, source document, and approval fingerprints; `ready_for_posting_review` alone never posts stock. The service does not implement adjustments or reversals. All writers that receive stock must follow the same PO-line locking protocol; external writers that bypass it remain a concurrency risk. Existing balance drift from earlier non-posting activity is not automatically repaired. Supplier-alias configuration is supplied outside the database and is not versioned in an authorization; policy changes require an operational review and fresh approval. PostgreSQL integer overflow rejects and rolls back a posting; balances never wrap silently.

Run the disposable PostgreSQL checks:

```sh
docker compose run --rm test python -m pytest -q tests/test_inventory_posting.py
docker compose run --rm test python scripts/run_posting_evaluation.py
```

The evaluation uses controlled invoice-visible values and staff-event test data. It never posts the stored OCR-imperfect fixtures and is not a measure of OCR or model accuracy.

## Staff review HTTP API (Task 15)

The FastAPI application in `meridian.api.app` connects upload, OCR, structured extraction, validation, trusted reconciliation, audited staff correction, explicit approval, and Task 14 posting. Uploads start as document/upload-attempt records; processing creates a canonical invoice only when supplier identity is unique. Original OCR and extraction stay immutable, while a revisioned review snapshot tracks corrections and fresh findings. Processing never approves or posts inventory. Staff approval binds a destination location and explicit PO-line allocations; the posting route delegates to Task 14's atomic service.

The default provider is the deterministic offline mock. All `/api/v1` routes, including read-only routes, fail closed without staff authentication; local bearer-token mode is opt-in and disabled by default. Multipart request bytes are bounded before parsing. The API imports without a database connection or OpenAI key. See the [API guide](docs/API.md) for migration, server startup, endpoints, storage, retry policy, security boundaries, and the disposable HTTP demo.

## Synthetic evaluation corpus (Task 17)

`output/corpus/` contains 70 entirely fictional invoices: ten each of clean, missing totals, blurry scan, PO quantity mismatch, duplicate identity, unknown product, and PO price anomaly. Three supplier templates occur 24, 23, and 23 times. `meridian.corpus` derives each case from master seed 2029 and a stable case ID, renders a selectable original PDF, and creates a 100 DPI image-only scanned PDF using an explicit scan profile. Invoice-visible inputs live in `inputs/`; evaluation-only mathematical truth and trusted supplier/catalog/PO/prior-invoice snapshots live in `ground_truth/`. Neither rendering nor scanning accepts a truth object. Syntactically valid synthetic UPC-A and normalized 11-digit NDC values make no claim of real registry membership.

Generate and validate the corpus, then score it with Tesseract and the deterministic offline mock:

```sh
docker compose run --rm test python scripts/generate_corpus.py
docker compose run --rm test python scripts/generate_corpus.py --validate-only
docker compose run --rm test python scripts/check_corpus_reproducibility.py
docker compose run --rm test python scripts/check_corpus_reproducibility.py --evaluate
docker compose run --rm test python scripts/evaluate_corpus.py
docker compose run --rm test python scripts/audit_corpus_report.py
```

The versioned manifest records relative artifact paths, SHA-256 hashes, seeds, supplier/layout distribution, scan profiles, and duplicate relationships. `output/corpus/reports/metrics.json` separates scanned OCR/mock observations from controlled invoice-visible rule checks; `analysis.md` summarizes failures. Controlled transcription has no OCR citations or confidence and is not an OCR/LLM accuracy score. The duplicate category uses the same supplier and invoice number as an earlier clean case with distinct bytes; its trusted prior history also includes an exact repeat hash to exercise both duplicate policies. No evaluation result is staff approval or inventory posting. Historical fixture and evaluation outputs remain untouched. See [evaluation plan](docs/EVALUATION_PLAN.md) for metric definitions and limitations.

The versioned corpus includes all 140 PDFs in the repository so manifest paths resolve immediately in a fresh checkout and CI can run the complete test suite without a generation pre-step. The 70 scans occupy about 6.8 MB, originals about 280 KB, and the full corpus about 11 MB; this bounded size is acceptable for the first corpus revision. Regeneration and byte-comparison commands remain available above. Avoid committing temporary regeneration copies or runtime caches.

The independent report audit recomputes per-case finding and review confusion counts, OCR micro-averaged edit rates, field denominators, extraction totals, and grouped metrics. The 90 controlled true positives are case/finding-code pairs: 30 missing-total codes, 30 PO/catalog discrepancy codes, and 30 duplicate-policy codes for ten duplicate cases. There are 50 underlying anomalous cases. Controlled local validation is a separate rule oracle, not a run of the production Task 12 validator; only controlled reconciliation calls production Task 13 code. The offline mock identifies just 29 of 203 printed product identifiers, a major limitation when interpreting its downstream findings.
