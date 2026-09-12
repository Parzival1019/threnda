"""Sources: read access, and creation (Milestone 2 — see workspaces.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_db
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace
from threnda.app.schemas.common import Page
from threnda.app.schemas.source import SourceCreate, SourceRead

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=Page[SourceRead])
def list_sources(
    workspace_id: int = Query(..., description="Workspace to scope results to."),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Page[SourceRead]:
    stmt = select(Source).where(Source.workspace_id == workspace_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Source.id).limit(limit).offset(offset)).scalars().all()
    return Page[SourceRead](
        items=[SourceRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=SourceRead, status_code=201)
def create_source(
    payload: SourceCreate,
    workspace_id: int = Query(..., description="Workspace this source belongs to."),
    db: Session = Depends(get_db),
) -> SourceRead:
    if db.get(Workspace, workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    source = Source(workspace_id=workspace_id, name=payload.name, source_type=payload.source_type)
    db.add(source)
    db.flush()
    return SourceRead.model_validate(source)
