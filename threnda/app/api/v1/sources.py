"""Sources: read access, and creation (Milestone 2 — see workspaces.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_db, require_analyst, require_viewer
from threnda.app.models.source import Source
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.schemas.common import Page
from threnda.app.schemas.source import SourceCreate, SourceRead

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=Page[SourceRead])
def list_sources(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    membership: WorkspaceMembership = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Page[SourceRead]:
    stmt = select(Source).where(Source.workspace_id == membership.workspace_id)
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
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> SourceRead:
    source = Source(
        workspace_id=membership.workspace_id, name=payload.name, source_type=payload.source_type
    )
    db.add(source)
    db.flush()
    return SourceRead.model_validate(source)
