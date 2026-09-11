"""Small shared helpers for models."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC ``datetime``.

    Used as a Python-side column default (rather than a database server
    default) so that the value is identical regardless of whether the
    underlying database is SQLite or PostgreSQL.
    """
    return datetime.now(UTC)
