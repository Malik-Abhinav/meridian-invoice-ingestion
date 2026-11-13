# Meridian Supplier Invoice Ingestion

An incremental, synthetic-data-only implementation of the AYC IT Healthcare take-home assessment. This repository currently contains the Python package foundation and its import test.

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

The editable install makes the `src/meridian` package importable while developing. The `test` extra installs pytest; the package has no runtime dependencies yet.
