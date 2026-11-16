# Meridian Supplier Invoice Ingestion

[![CI](https://github.com/Malik-Abhinav/meridian-invoice-ingestion/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Malik-Abhinav/meridian-invoice-ingestion/actions/workflows/ci.yml)

An incremental, synthetic-data-only supplier invoice ingestion project. The current implementation includes domain contracts, database models, PostgreSQL migrations with live constraint tests, and three selectable-text invoice PDF layouts.

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
