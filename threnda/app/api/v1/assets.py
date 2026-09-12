"""Read access to assets, and the Milestone 4 manual risk-scoring update."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_db
from threnda.app.models.asset import Asset
from threnda.app.models.enums import AssetEnvironment, AssetType
from threnda.app.schemas.asset import AssetRead, AssetUpdate
from threnda.app.schemas.common import Page
from threnda.app.services.assets import apply_asset_update

router = APIRouter(prefix="/assets", tags=["assets"])


def _get_asset_or_404(db: Session, workspace_id: int, asset_id: int) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None or asset.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


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


@router.patch("/{asset_id}", response_model=AssetRead)
def update_asset(
    asset_id: int,
    payload: AssetUpdate,
    workspace_id: int = Query(..., description="Workspace to scope this action to."),
    db: Session = Depends(get_db),
) -> AssetRead:
    asset = _get_asset_or_404(db, workspace_id, asset_id)
    changes = payload.model_dump(exclude_unset=True)
    apply_asset_update(
        db,
        asset,
        internet_facing=changes.get("internet_facing"),
        internet_facing_set="internet_facing" in changes,
        business_criticality=changes.get("business_criticality"),
        business_criticality_set="business_criticality" in changes,
    )
    return AssetRead.model_validate(asset)
