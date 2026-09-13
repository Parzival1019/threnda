"""Shared upload-handling logic: filename safety, importer selection, and the
detect -> parse -> persist sequence for one uploaded file.

Extracted from ``api/v1/imports.py`` so the JSON API and the server-rendered
web UI (Milestone 5) run exactly the same ingestion behaviour instead of two
independently-maintained copies of it. Nothing about detection, parsing,
persistence, transaction semantics or the differential/dedup engines changes
here — see docs/importers.md and docs/deduplication.md, which this module
does not alter.
"""

from __future__ import annotations

import json as jsonlib
from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy.orm import Session

from threnda.app.models.enums import ImportStatus, SourceType
from threnda.app.models.import_run import ImportRun
from threnda.app.models.source import Source
from threnda.app.services.ingestion.base import (
    DuplicateIdentityError,
    Importer,
    ImportFormatError,
    RowError,
)
from threnda.app.services.ingestion.csv_importer import CsvImporter
from threnda.app.services.ingestion.json_importer import JsonImporter
from threnda.app.services.ingestion.pentest_importer import PentestImporter
from threnda.app.services.ingestion.runner import compute_file_hash, persist_parsed_import
from threnda.app.services.ingestion.sarif_importer import SarifImporter

MAX_FILENAME_LENGTH = 255

SUPPORTED_SOURCE_TYPES = {
    SourceType.CSV,
    SourceType.JSON,
    SourceType.SARIF,
    SourceType.PENTEST_REPORT,
}


class UnsupportedSourceTypeError(Exception):
    """Uploads aren't implemented yet for this ``Source.source_type``."""

    def __init__(self, source_type: SourceType) -> None:
        self.source_type = source_type
        super().__init__(
            f"Uploads are not supported for source type {source_type.value!r} yet "
            f"(supported: {sorted(t.value for t in SUPPORTED_SOURCE_TYPES)})."
        )


class MissingCsvMappingError(Exception):
    """A CSV source has no saved column mapping and none was supplied with this upload."""


class InvalidCsvMappingError(Exception):
    """The supplied (or saved) CSV mapping is malformed."""


class InvalidCsvMappingJsonError(Exception):
    """The ``csv_mapping`` form field isn't valid JSON, or isn't a flat string map."""


class ContentDoesNotMatchFormatError(Exception):
    """The uploaded bytes don't look like the source's declared format."""

    def __init__(self, format_name: str, declared_type: SourceType) -> None:
        super().__init__(
            f"Uploaded content does not look like {format_name.upper()} — "
            f"this source is configured as {declared_type.value}."
        )


def sanitize_filename(raw_filename: str | None) -> str:
    """Reduce an uploaded filename to a safe display value.

    Never used for filesystem access — Community processes uploads fully
    in memory, never writing the original file to disk — but still reduced
    to a bare basename (discarding any directory components, however
    path-traversal-looking) and length-capped before being stored as
    ``ImportRun.filename``.
    """
    if not raw_filename:
        return "upload"
    name = PurePosixPath(raw_filename.replace("\\", "/")).name.strip()
    return (name or "upload")[:MAX_FILENAME_LENGTH]


def parse_csv_mapping_json(csv_mapping: str | None) -> dict[str, str] | None:
    """Parse the ``csv_mapping`` form field's JSON text into a flat string map."""
    if csv_mapping is None:
        return None
    try:
        parsed = jsonlib.loads(csv_mapping)
    except jsonlib.JSONDecodeError as exc:
        raise InvalidCsvMappingJsonError(f"csv_mapping is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
    ):
        raise InvalidCsvMappingJsonError("csv_mapping must be a flat JSON object of strings")
    return parsed


def build_importer(source: Source, csv_mapping: dict[str, str] | None) -> Importer:
    if source.source_type == SourceType.CSV:
        mapping = csv_mapping or (source.meta or {}).get("csv_mapping")
        if not mapping:
            raise MissingCsvMappingError
        try:
            return CsvImporter(mapping=mapping)
        except ValueError as exc:
            raise InvalidCsvMappingError(str(exc)) from exc
    if source.source_type == SourceType.JSON:
        return JsonImporter()
    if source.source_type == SourceType.SARIF:
        return SarifImporter()
    if source.source_type == SourceType.PENTEST_REPORT:
        return PentestImporter()
    raise UnsupportedSourceTypeError(source.source_type)


def _record_failed_import(
    db: Session, *, workspace_id: int, source_id: int, filename: str, raw: bytes
) -> ImportRun:
    """Record a durable ``FAILED`` ``ImportRun``.

    Shared by both ways a whole upload can be rejected: the file doesn't
    parse as its declared format at all (``ImportFormatError``), or it
    parses but contains two records claiming the same identity
    (``DuplicateIdentityError``). Either way, no ``Finding`` row is ever
    written — the caller gets an actionable, durable result instead of a
    raw database error or a silently partial import.
    """
    failed_run = ImportRun(
        workspace_id=workspace_id,
        source_id=source_id,
        filename=filename,
        file_hash=compute_file_hash(raw),
        status=ImportStatus.FAILED,
    )
    db.add(failed_run)
    db.flush()
    return failed_run


@dataclass
class UploadOutcome:
    """The result of one upload attempt — success or a durably-recorded failure."""

    import_run: ImportRun
    row_errors: list[RowError]
    failed: bool = False
    failure_detail: str | None = None


def handle_upload(
    db: Session,
    *,
    workspace_id: int,
    source: Source,
    filename: str | None,
    content_type: str | None,
    raw: bytes,
    csv_mapping: dict[str, str] | None,
) -> UploadOutcome:
    """Run detect -> parse -> persist for one uploaded file against ``source``.

    Never raises for an ordinary import-level failure (content that doesn't
    match the declared format, duplicate identities within the file) —
    those come back as ``UploadOutcome(failed=True, ...)`` with a durable
    ``FAILED`` ``ImportRun`` already written, exactly matching the JSON
    API's existing behaviour (see ``docs/importers.md``, "Transaction
    semantics"). Raises the typed errors above only for problems a caller's
    own form validation is expected to have already prevented
    (unsupported source type, missing/invalid CSV mapping) — the same
    conditions the JSON API rejects with a ``422`` *before* ever calling
    this.
    """
    importer = build_importer(source, csv_mapping)
    safe_filename = sanitize_filename(filename)

    if not importer.detect(safe_filename, content_type, raw):
        raise ContentDoesNotMatchFormatError(importer.format_name, source.source_type)

    try:
        parsed = importer.parse(raw)
    except ImportFormatError as exc:
        failed_run = _record_failed_import(
            db, workspace_id=workspace_id, source_id=source.id, filename=safe_filename, raw=raw
        )
        return UploadOutcome(
            import_run=failed_run, row_errors=[], failed=True, failure_detail=str(exc)
        )

    try:
        import_run = persist_parsed_import(
            db,
            workspace_id=workspace_id,
            source_id=source.id,
            filename=safe_filename,
            raw=raw,
            parsed=parsed,
        )
    except DuplicateIdentityError as exc:
        failed_run = _record_failed_import(
            db, workspace_id=workspace_id, source_id=source.id, filename=safe_filename, raw=raw
        )
        return UploadOutcome(
            import_run=failed_run, row_errors=[], failed=True, failure_detail=str(exc)
        )

    if csv_mapping is not None and source.source_type == SourceType.CSV:
        updated_meta = dict(source.meta or {})
        updated_meta["csv_mapping"] = csv_mapping
        source.meta = updated_meta

    return UploadOutcome(import_run=import_run, row_errors=list(parsed.errors))
