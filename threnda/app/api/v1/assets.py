"""Read-only access to assets."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_db
from threnda.app.models.asset import Asset
from threnda.app.models.enums import AssetEnvironment, AssetType
from threnda.app.schemas.asset import AssetRead
from threnda.app.schemas.common import Page

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("", response_model=Page[AssetRead])
def list_assets(
    workspace_id: int = Query(..., description="Workspace to scope results to."),
    asset_type: AssetType | None = None,
    environment: AssetEnvironment | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Page[AssetRead]:
    stmt = select(Asset).where(Asset.workspace_id == workspace_id)
    if asset_type is not None:
        stmt = stmt.where(Asset.asset_type == asset_type)
    if environment is not None:
        stmt = stmt.where(Asset.environment == environment)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Asset.id).limit(limit).offset(offset)).scalars().all()
    return Page[AssetRead](
        items=[AssetRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
