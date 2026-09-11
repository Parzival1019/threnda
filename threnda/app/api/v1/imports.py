"""Read-only access to import runs.

There is no ``POST /api/v1/imports`` yet — ingestion is Milestone 2. Import
runs currently only exist if something writes them directly (e.g. tests).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_db
from threnda.app.models.import_run import ImportRun
from threnda.app.schemas.common import Page
from threnda.app.schemas.import_run import ImportRunRead

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
