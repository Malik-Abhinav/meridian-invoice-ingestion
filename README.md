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

The editable install makes the `src/meridian` package importable while developing. The `test` extra installs pytest. SQLAlchemy is the current runtime dependency; database driver installation is optional for local package-only tests.

## Docker development environment

Docker Compose provides PostgreSQL 16 and a Python 3.12 test container. The database is reachable only on the private Compose network. Copy the development-only example settings before running Compose:

```sh
cp .env.example .env
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
