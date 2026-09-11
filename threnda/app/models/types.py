"""Custom column types shared across models.

``UTCDateTime`` exists because SQLite silently drops timezone information on
round trip even for a column declared ``DateTime(timezone=True)`` — it has
no native timezone-aware datetime type, so SQLAlchemy's SQLite dialect
returns a naive ``datetime`` after a value has actually gone through the
database. PostgreSQL's ``timestamptz`` does not have this problem. Since
Threnda stores every timestamp as UTC by convention (see
``threnda.app.models.mixins.utcnow``), this type normalizes both directions
so application code always sees a timezone-aware UTC ``datetime`` on both
databases, instead of the app having to special-case SQLite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """A timezone-aware ``DateTime`` that is always UTC, on any backend."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # SQLite has no native timezone-aware type; values it returns
            # are naive but were always written as UTC by this application.
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
