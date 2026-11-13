"""Database configuration and declarative metadata; no connection is made on import."""

import os

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    """Read the database URL at use time, without a credential fallback."""
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL must be configured before database access")
    return value
