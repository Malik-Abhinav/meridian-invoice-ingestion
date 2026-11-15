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
- **Limitations:** A boolean fictional marker cannot prove invented names or identifiers. Future fixture review must exclude real pharmacy data and proprietary materials. No PDF renderer, image processing, OCR, complete corpus, or scoring logic is present.
