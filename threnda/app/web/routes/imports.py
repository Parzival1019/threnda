"""Import history and the new-import wizard.

The real ``POST /api/v1/imports`` contract has no separate "preview before
commit" step — it parses, validates and persists in one transaction (see
docs/importers.md). The wizard here reflects that honestly: pick a source
and a file, upload (which commits immediately), see the result. The one
extra step is CSV column mapping, needed only because a CSV source with no
saved mapping yet requires one before ``handle_upload`` can run at all —
handled by a small htmx round trip (``POST /imports/sniff``) that reads
just the file's header row server-side (reusing the exact same decode path
``CsvImporter.parse`` uses) and pre-fills a mapping table with the
existing ``guess_mapping`` heuristic, rather than duplicating any of that
parsing logic in client-side JavaScript.
"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.responses import Response

from threnda.app.api.deps import get_db
from threnda.app.models.enums import SourceType
from threnda.app.models.import_run import ImportRun
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace
from threnda.app.services.ingestion.csv_importer import KNOWN_TARGET_FIELDS, guess_mapping
from threnda.app.services.ingestion.upload import (
    ContentDoesNotMatchFormatError,
    InvalidCsvMappingError,
    MissingCsvMappingError,
    UnsupportedSourceTypeError,
    handle_upload,
)
from threnda.app.web.csrf import apply_csrf_cookie, get_csrf_token, verify_csrf
from threnda.app.web.deps import build_shell_context, get_current_workspace, templates

router = APIRouter(prefix="/imports", tags=["web-imports"])

PAGE_SIZE = 50
UPLOADABLE_SOURCE_TYPES = (
    SourceType.CSV,
    SourceType.JSON,
    SourceType.SARIF,
    SourceType.PENTEST_REPORT,
)


def _read_csv_header(raw: bytes) -> list[str] | None:
    """The header row only — same decode path as ``CsvImporter.parse``.

    Returns ``None`` if the file has no header row or isn't valid UTF-8;
    the caller degrades to "no columns detected" rather than erroring,
    since the real validation happens later, in the actual upload.
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    header_row = next(csv.reader(io.StringIO(text)), None)
    return [column.strip() for column in header_row] if header_row else None


@router.get("")
def list_imports(
    request: Request,
    offset: int = 0,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> Response:
    stmt = (
        select(ImportRun)
        .where(ImportRun.workspace_id == workspace.id)
        .order_by(ImportRun.started_at.desc())
        .limit(PAGE_SIZE)
        .offset(offset)
    )
    runs = db.execute(stmt).scalars().all()
    context = {
        "active_nav": "imports",
        "runs": runs,
        "offset": offset,
        "page_size": PAGE_SIZE,
        **build_shell_context(db, workspace),
    }
    return templates.TemplateResponse(request, "imports/list.html", context)


@router.get("/new")
def new_import_form(
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> Response:
    sources = (
        db.execute(
            select(Source)
            .where(
                Source.workspace_id == workspace.id, Source.source_type.in_(UPLOADABLE_SOURCE_TYPES)
            )
            .order_by(Source.name)
        )
        .scalars()
        .all()
    )

    token = get_csrf_token(request)
    context = {
        "active_nav": "imports",
        "sources": sources,
        "csrf_token": token,
        **build_shell_context(db, workspace),
    }
    response = templates.TemplateResponse(request, "imports/new.html", context)
    apply_csrf_cookie(request, response, token)
    return response


@router.post("/sniff")
async def sniff_csv_header(
    request: Request,
    source_id: int = Form(...),
    file: UploadFile = File(...),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> Response:
    """htmx partial: a CSV mapping table pre-filled from the file's header row.

    Returns an empty fragment for a non-CSV source, or one that already has
    a saved mapping — no mapping step is needed in either case.
    """
    source = db.get(Source, source_id)
    if source is None or source.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Source not found in this workspace")

    if source.source_type != SourceType.CSV or (source.meta or {}).get("csv_mapping"):
        return templates.TemplateResponse(request, "imports/_mapping_fields.html", {"columns": []})

    raw = await file.read()
    headers = _read_csv_header(raw) or []
    guessed = guess_mapping(headers)
    columns = [{"name": h, "guess": guessed.get(h, "")} for h in headers]
    context = {"columns": columns, "target_fields": sorted(KNOWN_TARGET_FIELDS)}
    return templates.TemplateResponse(request, "imports/_mapping_fields.html", context)


@router.post("/new")
async def create_import(
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> Response:
    form = await request.form()
    csrf_value = form.get("csrf_token")
    verify_csrf(request, str(csrf_value) if csrf_value is not None else None)

    source_id_raw = form.get("source_id")
    upload = form.get("file")
    if not source_id_raw or not isinstance(upload, StarletteUploadFile):
        raise HTTPException(status_code=422, detail="A source and a file are required.")

    source = db.get(Source, int(str(source_id_raw)))
    if source is None or source.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Source not found in this workspace")

    csv_mapping: dict[str, str] | None = None
    mapping_columns = {
        key.removeprefix("mapping_column_"): str(value)
        for key, value in form.multi_items()
        if key.startswith("mapping_column_")
    }
    if mapping_columns:
        csv_mapping = {}
        for index, column in mapping_columns.items():
            target = form.get(f"mapping_target_{index}")
            if target:
                csv_mapping[column] = str(target)

    raw = await upload.read()

    try:
        outcome = handle_upload(
            db,
            workspace_id=workspace.id,
            source=source,
            filename=upload.filename,
            content_type=upload.content_type,
            raw=raw,
            csv_mapping=csv_mapping,
        )
    except MissingCsvMappingError as exc:
        raise HTTPException(
            status_code=422,
            detail="This CSV source has no saved column mapping yet — map at least "
            'the "title" column.',
        ) from exc
    except (
        InvalidCsvMappingError,
        UnsupportedSourceTypeError,
        ContentDoesNotMatchFormatError,
    ) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    token = get_csrf_token(request)
    context = {
        "active_nav": "imports",
        "outcome": outcome,
        "source": source,
        "csrf_token": token,
        **build_shell_context(db, workspace),
    }
    response = templates.TemplateResponse(request, "imports/_result.html", context)
    apply_csrf_cookie(request, response, token)
    return response


@router.get("/{import_run_id}")
def import_detail(
    request: Request,
    import_run_id: int,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> Response:
    import_run = db.get(ImportRun, import_run_id)
    if import_run is None or import_run.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Import not found")
    context = {
        "active_nav": "imports",
        "run": import_run,
        **build_shell_context(db, workspace),
    }
    return templates.TemplateResponse(request, "imports/detail.html", context)
