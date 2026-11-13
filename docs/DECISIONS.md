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
