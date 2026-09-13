"""Import runs: read access, and the upload endpoint that creates them.

See docs/importers.md for the full contract (supported formats, CSV mapping,
transaction semantics). Community only ever reads a file the caller uploads
— nothing here fetches a URL, path or external reference named inside it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_db
from threnda.app.models.import_run import ImportRun
from threnda.app.models.source import Source
from threnda.app.schemas.common import Page
from threnda.app.schemas.import_run import ImportRowErrorRead, ImportRunRead, ImportUploadResult
from threnda.app.services.ingestion.upload import (
    ContentDoesNotMatchFormatError,
    InvalidCsvMappingError,
    InvalidCsvMappingJsonError,
    MissingCsvMappingError,
    UnsupportedSourceTypeError,
    handle_upload,
    parse_csv_mapping_json,
)

router = APIRouter(prefix="/imports", tags=["imports"])


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
    file: UploadFile = File(
        ..., description="The CSV, JSON, SARIF, or structured pentest JSON file to import."
    ),
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

    try:
        parsed_mapping = parse_csv_mapping_json(csv_mapping)
    except InvalidCsvMappingJsonError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    raw = await file.read()

    try:
        outcome = handle_upload(
            db,
            workspace_id=workspace_id,
            source=source,
            filename=file.filename,
            content_type=file.content_type,
            raw=raw,
            csv_mapping=parsed_mapping,
        )
    except MissingCsvMappingError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                "This CSV source has no saved column mapping yet. Provide "
                '"csv_mapping" (a JSON object of {"column name": "field"}) '
                "with this upload."
            ),
        ) from exc
    except (InvalidCsvMappingError, UnsupportedSourceTypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ContentDoesNotMatchFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if outcome.failed:
        # The upload API catches an import-level failure itself and returns a
        # JSONResponse directly instead of raising — the request handler
        # completes normally, so the session commits the FAILED ImportRun it
        # already wrote, instead of the generic per-request rollback-on-
        # exception undoing it. Documented in docs/importers.md under
        # "Transaction semantics".
        return JSONResponse(
            status_code=422,
            content={
                "detail": outcome.failure_detail,
                "import_run": ImportRunRead.model_validate(outcome.import_run).model_dump(
                    mode="json"
                ),
            },
        )

    return ImportUploadResult(
        import_run=ImportRunRead.model_validate(outcome.import_run),
        row_errors=[
            ImportRowErrorRead(index=error.index, message=error.message)
            for error in outcome.row_errors
        ],
    )
