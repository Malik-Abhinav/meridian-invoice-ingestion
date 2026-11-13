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
