"""CSV/JSON export of a workspace's exposures.

Schema (stable, documented in docs/exports.md) — one row/object per
exposure, in this exact column order:

``id, canonical_fingerprint, title, severity, risk_score, status, owner,
acknowledged, remediation_target_date, remediation, asset, sources,
first_seen, last_seen, resolved_at, created_at, updated_at``

Rows are fetched via keyset pagination (``id > last_id``, in batches — see
``_EXPORT_BATCH_SIZE``) rather than one unbounded query, so a workspace
with a very large number of exposures is exported with bounded memory,
never a hard cap on how many rows are returned (CLAUDE.md: "Do not
introduce artificial asset or finding limits").
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from threnda.app.models.exposure import Exposure
from threnda.app.models.finding import Finding

_EXPORT_BATCH_SIZE = 500

EXPORT_COLUMNS = (
    "id",
    "canonical_fingerprint",
    "title",
    "affected_system",
    "description",
    "severity",
    "risk_score",
    "status",
    "owner",
    "acknowledged",
    "remediation_target_date",
    "remediation",
    "asset",
    "sources",
    "first_seen",
    "last_seen",
    "resolved_at",
    "created_at",
    "updated_at",
)

# Leading characters Excel/Google Sheets treat as the start of a formula.
# See OWASP's CSV Injection cheat sheet.
_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")


def iter_workspace_exposures(db: Session, workspace_id: int) -> Iterator[Exposure]:
    """Yield every exposure in a workspace, oldest id first, in bounded batches."""
    last_id = 0
    while True:
        batch = (
            db.execute(
                select(Exposure)
                .where(Exposure.workspace_id == workspace_id, Exposure.id > last_id)
                .options(
                    selectinload(Exposure.findings).selectinload(Finding.asset),
                    selectinload(Exposure.findings).selectinload(Finding.source),
                )
                .order_by(Exposure.id)
                .limit(_EXPORT_BATCH_SIZE)
            )
            .scalars()
            .all()
        )
        if not batch:
            return
        yield from batch
        last_id = batch[-1].id


def _asset_and_sources(exposure: Exposure) -> tuple[str, str]:
    asset_names = sorted({f.asset.name for f in exposure.findings if f.asset is not None})
    source_names = sorted({f.source.name for f in exposure.findings if f.source is not None})
    return ", ".join(asset_names), ", ".join(source_names)


def exposure_to_row(exposure: Exposure) -> dict[str, Any]:
    asset, sources = _asset_and_sources(exposure)
    return {
        "id": exposure.id,
        "canonical_fingerprint": exposure.canonical_fingerprint,
        "title": exposure.title,
        "affected_system": exposure.affected_system,
        "description": exposure.description,
        "severity": exposure.severity.value,
        "risk_score": exposure.risk_score,
        "status": exposure.status.value,
        "owner": exposure.owner,
        "acknowledged": exposure.acknowledged,
        "remediation_target_date": (
            exposure.remediation_target_date.isoformat()
            if exposure.remediation_target_date
            else None
        ),
        "remediation": exposure.remediation,
        "asset": asset,
        "sources": sources,
        "first_seen": exposure.first_seen.isoformat(),
        "last_seen": exposure.last_seen.isoformat(),
        "resolved_at": exposure.resolved_at.isoformat() if exposure.resolved_at else None,
        "created_at": exposure.created_at.isoformat(),
        "updated_at": exposure.updated_at.isoformat(),
    }


def _csv_safe(value: Any) -> Any:
    """Neutralize a value that would otherwise open as a spreadsheet formula.

    Prefixing a leading ``'`` is the standard mitigation (OWASP CSV
    Injection): Excel/Sheets render it literally instead of evaluating the
    cell as a formula, and it round-trips back to plain text on re-import
    elsewhere. Only strings are checked — numbers/booleans/``None`` can
    never start with a trigger character.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_TRIGGER_CHARS):
        return "'" + value
    return value


def iter_csv_lines(db: Session, workspace_id: int) -> Iterator[str]:
    """Yield the export as CSV text, one or more lines at a time.

    Each yielded chunk is written through Python's ``csv`` module (never
    hand-built string concatenation), so quoting/escaping of embedded
    commas, quotes, and newlines in a title or owner field is always
    correct.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(EXPORT_COLUMNS))
    writer.writeheader()
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    for exposure in iter_workspace_exposures(db, workspace_id):
        row = {key: _csv_safe(value) for key, value in exposure_to_row(exposure).items()}
        writer.writerow(row)
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


def iter_json_array(db: Session, workspace_id: int) -> Iterator[str]:
    """Yield the export as a single JSON array, streamed one object at a time."""
    yield "["
    first = True
    for exposure in iter_workspace_exposures(db, workspace_id):
        prefix = "" if first else ","
        first = False
        yield prefix + json.dumps(exposure_to_row(exposure))
    yield "]"
