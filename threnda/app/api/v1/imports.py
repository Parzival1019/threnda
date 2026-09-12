"""Import runs: read access, and the upload endpoint that creates them.

See docs/importers.md for the full contract (supported formats, CSV mapping,
transaction semantics). Community only ever reads a file the caller uploads
— nothing here fetches a URL, path or external reference named inside it.
"""

from __future__ import annotations

import json as jsonlib
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_db
from threnda.app.models.enums import ImportStatus, SourceType
from threnda.app.models.import_run import ImportRun
from threnda.app.models.source import Source
from threnda.app.schemas.common import Page
from threnda.app.schemas.import_run import ImportRowErrorRead, ImportRunRead, ImportUploadResult
from threnda.app.services.ingestion.base import DuplicateIdentityError, Importer, ImportFormatError
from threnda.app.services.ingestion.csv_importer import CsvImporter
from threnda.app.services.ingestion.json_importer import JsonImporter
from threnda.app.services.ingestion.runner import compute_file_hash, persist_parsed_import
from threnda.app.services.ingestion.sarif_importer import SarifImporter

router = APIRouter(prefix="/imports", tags=["imports"])

MAX_FILENAME_LENGTH = 255

_SUPPORTED_SOURCE_TYPES = {SourceType.CSV, SourceType.JSON, SourceType.SARIF}


def _sanitize_filename(raw_filename: str | None) -> str:
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


def _build_importer(source: Source, csv_mapping: dict[str, str] | None) -> Importer:
    if source.source_type == SourceType.CSV:
        mapping = csv_mapping or (source.meta or {}).get("csv_mapping")
        if not mapping:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This CSV source has no saved column mapping yet. Provide "
                    '"csv_mapping" (a JSON object of {"column name": "field"}) '
                    "with this upload."
                ),
            )
        try:
            return CsvImporter(mapping=mapping)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if source.source_type == SourceType.JSON:
        return JsonImporter()
    if source.source_type == SourceType.SARIF:
        return SarifImporter()
    raise HTTPException(
        status_code=422,
        detail=(
            f"Uploads are not supported for source type {source.source_type.value!r} yet "
            f"(supported: {sorted(t.value for t in _SUPPORTED_SOURCE_TYPES)})."
        ),
    )


def _record_failed_import(
    db: Session,
    *,
    workspace_id: int,
    source_id: int,
    filename: str,
    raw: bytes,
    detail: str,
) -> JSONResponse:
    """Record a durable ``FAILED`` ``ImportRun`` and report ``422``.

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
    return JSONResponse(
        status_code=422,
        content={
            "detail": detail,
            "import_run": ImportRunRead.model_validate(failed_run).model_dump(mode="json"),
        },
    )


def _parse_csv_mapping(csv_mapping: str | None) -> dict[str, str] | None:
    if csv_mapping is None:
        return None
    try:
        parsed = jsonlib.loads(csv_mapping)
    except jsonlib.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422, detail=f"csv_mapping is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
    ):
        raise HTTPException(
            status_code=422, detail="csv_mapping must be a flat JSON object of strings"
        )
    return parsed


@router.get("", response_model=Page[ImportRunRead])
def list_imports(
    workspace_id: int = Query(..., description="Workspace to scope results to."),
    source_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Page[ImportRunRead]:
    stmt = select(ImportRun).where(ImportRun.workspace_id == workspace_id)
    if source_id is not None:
        stmt = stmt.where(ImportRun.source_id == source_id)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(stmt.order_by(ImportRun.started_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return Page[ImportRunRead](
        items=[ImportRunRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ImportUploadResult, status_code=201)
async def create_import(
    workspace_id: int = Query(..., description="Workspace this import belongs to."),
    source_id: int = Form(..., description="Source (within that workspace) this upload is for."),
    file: UploadFile = File(..., description="The CSV, JSON or SARIF file to import."),
    csv_mapping: str | None = Form(
        default=None,
        description=(
            "For CSV sources only: a JSON object mapping column names to "
            'fields, e.g. {"Vulnerability": "title", "Host": "asset"}. '
            "Required the first time a CSV source is used; after that, the "
            "previously saved mapping is reused unless this overrides it — "
            "and when given, this value replaces the saved mapping."
        ),
    ),
    db: Session = Depends(get_db),
) -> ImportUploadResult | JSONResponse:
    source = db.get(Source, source_id)
    if source is None or source.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Source not found in this workspace")

    parsed_mapping = _parse_csv_mapping(csv_mapping)
    importer = _build_importer(source, parsed_mapping)

    safe_filename = _sanitize_filename(file.filename)
    raw = await file.read()

    if not importer.detect(safe_filename, file.content_type, raw):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Uploaded content does not look like {importer.format_name.upper()} — "
                f"this source is configured as {source.source_type.value}."
            ),
        )

    try:
        parsed = importer.parse(raw)
    except ImportFormatError as exc:
        # The file itself doesn't parse as the declared format at all: record
        # a FAILED ImportRun (a real audit entry — not rolled back, since the
        # request handler returns normally rather than raising) and report
        # the failure without ever touching Finding/Asset rows.
        return _record_failed_import(
            db,
            workspace_id=workspace_id,
            source_id=source_id,
            filename=safe_filename,
            raw=raw,
            detail=str(exc),
        )

    try:
        import_run = persist_parsed_import(
            db,
            workspace_id=workspace_id,
            source_id=source_id,
            filename=safe_filename,
            raw=raw,
            parsed=parsed,
        )
    except DuplicateIdentityError as exc:
        # Same durable-failure treatment as an ImportFormatError above:
        # persist_parsed_import raises this before writing anything, so
        # there is nothing to roll back here either.
        return _record_failed_import(
            db,
            workspace_id=workspace_id,
            source_id=source_id,
            filename=safe_filename,
            raw=raw,
            detail=str(exc),
        )

    if parsed_mapping is not None and source.source_type == SourceType.CSV:
        updated_meta = dict(source.meta or {})
        updated_meta["csv_mapping"] = parsed_mapping
        source.meta = updated_meta

    return ImportUploadResult(
        import_run=ImportRunRead.model_validate(import_run),
        row_errors=[
            ImportRowErrorRead(index=error.index, message=error.message) for error in parsed.errors
        ],
    )
