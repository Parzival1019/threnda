"""Review queue for POSSIBLE-confidence duplicate candidates.

See docs/deduplication.md. A ``POSSIBLE`` match (Layer 2 fuzzy similarity in
the uncertain band — see ``threnda.app.services.dedup.correlation``) is
*never* auto-merged; it lands here for a human to explicitly merge or keep
separate. There is no endpoint that merges automatically or in bulk by
design — every resolution here is a deliberate, individually-recorded
decision.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_db
from threnda.app.models.enums import PossibleDuplicateStatus
from threnda.app.models.finding import Finding
from threnda.app.models.mixins import utcnow
from threnda.app.models.possible_duplicate import PossibleDuplicate
from threnda.app.schemas.common import Page
from threnda.app.schemas.possible_duplicate import PossibleDuplicateRead
from threnda.app.services.dedup.correlation import (
    create_standalone_exposure,
    link_finding_to_exposure,
)

router = APIRouter(prefix="/possible-duplicates", tags=["possible-duplicates"])


def _get_pending_or_404(
    db: Session, workspace_id: int, possible_duplicate_id: int
) -> PossibleDuplicate:
    candidate = db.get(PossibleDuplicate, possible_duplicate_id)
    if candidate is None or candidate.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Possible duplicate not found")
    if candidate.status != PossibleDuplicateStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"Already resolved as {candidate.status.value!r}; nothing left to decide.",
        )
    return candidate


@router.get("", response_model=Page[PossibleDuplicateRead])
def list_possible_duplicates(
    workspace_id: int = Query(..., description="Workspace to scope results to."),
    status: PossibleDuplicateStatus | None = Query(
        default=PossibleDuplicateStatus.PENDING,
        description="Defaults to only the pending review queue.",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Page[PossibleDuplicateRead]:
    stmt = select(PossibleDuplicate).where(PossibleDuplicate.workspace_id == workspace_id)
    if status is not None:
        stmt = stmt.where(PossibleDuplicate.status == status)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(stmt.order_by(PossibleDuplicate.id).limit(limit).offset(offset)).scalars().all()
    )
    return Page[PossibleDuplicateRead](
        items=[PossibleDuplicateRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/{possible_duplicate_id}/merge", response_model=PossibleDuplicateRead)
def merge_possible_duplicate(
    possible_duplicate_id: int,
    workspace_id: int = Query(..., description="Workspace to scope this action to."),
    actor: str = Query(
        default="system",
        description="Placeholder actor identity until authentication exists (Milestone 7).",
    ),
    db: Session = Depends(get_db),
) -> PossibleDuplicateRead:
    """Confirm the candidate: link the finding to the candidate exposure."""
    candidate = _get_pending_or_404(db, workspace_id, possible_duplicate_id)
    finding = db.get(Finding, candidate.finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="The finding behind this candidate is gone")

    link_finding_to_exposure(
        db, finding, candidate.candidate_exposure_id, confidence="possible-merged", actor=actor
    )
    candidate.status = PossibleDuplicateStatus.MERGED
    candidate.resolved_at = utcnow()
    candidate.resolved_by = actor
    db.flush()
    return PossibleDuplicateRead.model_validate(candidate)


@router.post("/{possible_duplicate_id}/keep-separate", response_model=PossibleDuplicateRead)
def keep_possible_duplicate_separate(
    possible_duplicate_id: int,
    workspace_id: int = Query(..., description="Workspace to scope this action to."),
    actor: str = Query(
        default="system",
        description="Placeholder actor identity until authentication exists (Milestone 7).",
    ),
    db: Session = Depends(get_db),
) -> PossibleDuplicateRead:
    """Reject the candidate: give the finding its own standalone exposure."""
    candidate = _get_pending_or_404(db, workspace_id, possible_duplicate_id)
    finding = db.get(Finding, candidate.finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="The finding behind this candidate is gone")

    create_standalone_exposure(db, finding)
    candidate.status = PossibleDuplicateStatus.KEPT_SEPARATE
    candidate.resolved_at = utcnow()
    candidate.resolved_by = actor
    db.flush()
    return PossibleDuplicateRead.model_validate(candidate)
