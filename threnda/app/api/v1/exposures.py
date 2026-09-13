"""Read and update access to exposures — the user-facing security queue.

``PATCH /{exposure_id}`` is the "sound foundation for updates" asked for by
the Milestone 1 brief: it applies partial updates and writes an
``ExposureHistory`` audit row for every meaningful change, which is the
behaviour the rest of the product (Milestone 4 onward) builds on.
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_db, require_analyst, require_viewer
from threnda.app.models.enums import ExposureStatus, SeverityLevel
from threnda.app.models.exposure import Exposure
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.schemas.common import Page
from threnda.app.schemas.exposure import (
    ExposureDetailRead,
    ExposureRead,
    ExposureUpdate,
    RiskScoreComponentRead,
)
from threnda.app.services.exposures import (
    ExposureChanges,
    InvalidStatusTransitionError,
    apply_exposure_update,
)
from threnda.app.services.risk_scoring import compute_risk_score

router = APIRouter(prefix="/exposures", tags=["exposures"])


def _get_exposure_or_404(db: Session, workspace_id: int, exposure_id: int) -> Exposure:
    """Fetch an exposure scoped to a workspace.

    Returns 404 (not 403) when the exposure exists but belongs to a
    different workspace — this cannot happen for an authenticated caller
    reached through ``require_viewer``/``require_analyst`` (which already
    proves workspace membership), but it protects against a stale/reused
    exposure id from a workspace the caller has since lost access to.
    """
    exposure = db.get(Exposure, exposure_id)
    if exposure is None or exposure.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Exposure not found")
    return exposure


@router.get("", response_model=Page[ExposureRead])
def list_exposures(
    status: ExposureStatus | None = None,
    severity: SeverityLevel | None = None,
    owner: str | None = None,
    q: str | None = Query(default=None, description="Case-insensitive search over the title."),
    min_risk_score: int | None = Query(default=None, ge=0, le=100),
    max_risk_score: int | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    membership: WorkspaceMembership = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Page[ExposureRead]:
    stmt = select(Exposure).where(Exposure.workspace_id == membership.workspace_id)
    if status is not None:
        stmt = stmt.where(Exposure.status == status)
    if severity is not None:
        stmt = stmt.where(Exposure.severity == severity)
    if owner is not None:
        stmt = stmt.where(Exposure.owner == owner)
    if q:
        stmt = stmt.where(Exposure.title.ilike(f"%{q}%"))
    if min_risk_score is not None:
        stmt = stmt.where(Exposure.risk_score >= min_risk_score)
    if max_risk_score is not None:
        stmt = stmt.where(Exposure.risk_score <= max_risk_score)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    stmt = stmt.order_by(Exposure.risk_score.desc(), Exposure.id).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return Page[ExposureRead](
        items=[ExposureRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{exposure_id}", response_model=ExposureDetailRead)
def get_exposure(
    exposure_id: int,
    membership: WorkspaceMembership = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> ExposureDetailRead:
    """Fetch one exposure, including a live risk-score breakdown.

    ``risk_score``/``risk_score_breakdown`` here are computed fresh on
    every call rather than read from the persisted column — see
    ``threnda.app.services.risk_scoring``'s module docstring for why: this
    is the one place staleness (an age-bucket boundary crossed with no
    other mutation since) can never show through.
    """
    exposure = _get_exposure_or_404(db, membership.workspace_id, exposure_id)
    result = compute_risk_score(exposure)
    base = ExposureRead.model_validate(exposure).model_dump()
    base["risk_score"] = result.total
    return ExposureDetailRead(
        **base,
        risk_score_breakdown=[
            RiskScoreComponentRead(label=c.label, points=c.points, detail=c.detail)
            for c in result.components
        ],
    )


@router.patch("/{exposure_id}", response_model=ExposureRead)
def update_exposure(
    exposure_id: int,
    payload: ExposureUpdate,
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> ExposureRead:
    exposure = _get_exposure_or_404(db, membership.workspace_id, exposure_id)
    changes = cast(ExposureChanges, payload.model_dump(exclude_unset=True, exclude={"note"}))
    try:
        apply_exposure_update(
            db, exposure, changes, note=payload.note, actor=membership.user.username
        )
    except InvalidStatusTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ExposureRead.model_validate(exposure)
