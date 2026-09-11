"""Read-only access to ingestion sources."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_db
from threnda.app.models.source import Source
from threnda.app.schemas.common import Page
from threnda.app.schemas.source import SourceRead

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
