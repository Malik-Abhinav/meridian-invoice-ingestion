# Meridian Supplier Invoice Ingestion

[![CI](https://github.com/Malik-Abhinav/meridian-invoice-ingestion/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Malik-Abhinav/meridian-invoice-ingestion/actions/workflows/ci.yml)

An incremental, synthetic-data-only supplier invoice ingestion project. The current implementation includes invoice generation, OCR, structured extraction, and deterministic invoice-local validation. Purchase-order matching, approval, and inventory posting remain future work.

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

The [manifest](output/evaluation/manifest.json) maps each case ID to its files in [output/evaluation](output/evaluation/), records the degradation profile, and lists expected findings and a processing decision. Run the command from any directory; it always writes to the repository's `output/evaluation/`. The fixed seed is 2029 and resolution is 150 DPI. Repeating generation in the same environment produces byte-identical files. Ground truth contains expected PO and catalog values but is never passed to the PDF or scan renderer. Clean cases are only `auto_post_candidate`; no approval or inventory posting is implemented. This is a small evaluation fixture set, not the planned 60-invoice corpus.

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
