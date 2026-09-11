"""Read and update access to exposures — the user-facing security queue.

``PATCH /{exposure_id}`` is the "sound foundation for updates" asked for by
the Milestone 1 brief: it applies partial updates and writes an
``ExposureHistory`` audit row for every meaningful change, which is the
behaviour the rest of the product (Milestone 4 onward) builds on.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_db
from threnda.app.models.enums import ExposureHistoryEventType, ExposureStatus, SeverityLevel
from threnda.app.models.exposure import Exposure
from threnda.app.models.exposure_history import ExposureHistory
from threnda.app.models.mixins import utcnow
from threnda.app.schemas.common import Page
from threnda.app.schemas.exposure import ExposureRead, ExposureUpdate

router = APIRouter(prefix="/exposures", tags=["exposures"])


def _get_exposure_or_404(db: Session, workspace_id: int, exposure_id: int) -> Exposure:
    """Fetch an exposure scoped to a workspace.

    Returns 404 (not 403) when the exposure exists but belongs to a
    different workspace — until Milestone 7's authentication lands, callers
    supply ``workspace_id`` themselves, so this boundary is not yet an
    access control mechanism, but it does prevent cross-workspace ID
    enumeration from leaking whether a given ID exists elsewhere.
    """
    exposure = db.get(Exposure, exposure_id)
    if exposure is None or exposure.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Exposure not found")
    return exposure


@router.get("", response_model=Page[ExposureRead])
def list_exposures(
    workspace_id: int = Query(..., description="Workspace to scope results to."),
    status: ExposureStatus | None = None,
    severity: SeverityLevel | None = None,
    owner: str | None = None,
    q: str | None = Query(default=None, description="Case-insensitive search over the title."),
    min_risk_score: int | None = Query(default=None, ge=0, le=100),
    max_risk_score: int | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Page[ExposureRead]:
    stmt = select(Exposure).where(Exposure.workspace_id == workspace_id)
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


@router.get("/{exposure_id}", response_model=ExposureRead)
def get_exposure(
    exposure_id: int,
    workspace_id: int = Query(..., description="Workspace to scope results to."),
    db: Session = Depends(get_db),
) -> ExposureRead:
    exposure = _get_exposure_or_404(db, workspace_id, exposure_id)
    return ExposureRead.model_validate(exposure)


@router.patch("/{exposure_id}", response_model=ExposureRead)
def update_exposure(
    exposure_id: int,
    payload: ExposureUpdate,
    workspace_id: int = Query(..., description="Workspace to scope results to."),
    actor: str = Query(
        default="system",
        description="Placeholder actor identity until authentication exists (Milestone 7).",
    ),
    db: Session = Depends(get_db),
) -> ExposureRead:
    exposure = _get_exposure_or_404(db, workspace_id, exposure_id)
    changes = payload.model_dump(exclude_unset=True, exclude={"note"})
    history: list[ExposureHistory] = []

    if "status" in changes and changes["status"] != exposure.status:
        previous_status = exposure.status
        new_status = changes["status"]
        event_type = ExposureHistoryEventType.STATUS_CHANGED
        if new_status == ExposureStatus.RESOLVED:
            exposure.resolved_at = utcnow()
            event_type = ExposureHistoryEventType.RESOLVED
        elif previous_status == ExposureStatus.RESOLVED:
            exposure.resolved_at = None
            event_type = ExposureHistoryEventType.REOPENED
        exposure.status = new_status
        history.append(
            ExposureHistory(
                exposure=exposure,
                event_type=event_type,
                previous_value=previous_status.value,
                new_value=new_status.value,
                actor=actor,
            )
        )

    if "owner" in changes and changes["owner"] != exposure.owner:
        history.append(
            ExposureHistory(
                exposure=exposure,
                event_type=ExposureHistoryEventType.OWNER_CHANGED,
                previous_value=exposure.owner,
                new_value=changes["owner"],
                actor=actor,
            )
        )
        exposure.owner = changes["owner"]

    if "acknowledged" in changes and changes["acknowledged"] != exposure.acknowledged:
        exposure.acknowledged = changes["acknowledged"]
        if exposure.acknowledged:
            history.append(
                ExposureHistory(
                    exposure=exposure,
                    event_type=ExposureHistoryEventType.ACKNOWLEDGED,
                    previous_value="false",
                    new_value="true",
                    actor=actor,
                )
            )

    if "remediation_target_date" in changes and (
        changes["remediation_target_date"] != exposure.remediation_target_date
    ):
        previous_date = exposure.remediation_target_date
        new_date = changes["remediation_target_date"]
        history.append(
            ExposureHistory(
                exposure=exposure,
                event_type=ExposureHistoryEventType.REMEDIATION_DATE_CHANGED,
                previous_value=previous_date.isoformat() if previous_date else None,
                new_value=new_date.isoformat() if new_date else None,
                actor=actor,
            )
        )
        exposure.remediation_target_date = new_date

    if "remediation" in changes and changes["remediation"] != exposure.remediation:
        exposure.remediation = changes["remediation"]

    if payload.note:
        history.append(
            ExposureHistory(
                exposure=exposure,
                event_type=ExposureHistoryEventType.NOTE_ADDED,
                previous_value=None,
                new_value=payload.note,
                actor=actor,
            )
        )

    db.add_all(history)
    db.flush()
    db.refresh(exposure)
    return ExposureRead.model_validate(exposure)
