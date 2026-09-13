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

from threnda.app.api.deps import get_db, require_analyst, require_viewer
from threnda.app.models.enums import PossibleDuplicateStatus
from threnda.app.models.possible_duplicate import PossibleDuplicate
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.schemas.common import Page
from threnda.app.schemas.possible_duplicate import PossibleDuplicateRead
from threnda.app.services.possible_duplicates import (
    LinkedFindingMissingError,
    PossibleDuplicateAlreadyResolvedError,
    PossibleDuplicateNotFoundError,
    resolve_keep_separate,
    resolve_merge,
)

router = APIRouter(prefix="/possible-duplicates", tags=["possible-duplicates"])


@router.get("", response_model=Page[PossibleDuplicateRead])
def list_possible_duplicates(
    status: PossibleDuplicateStatus | None = Query(
        default=PossibleDuplicateStatus.PENDING,
        description="Defaults to only the pending review queue.",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    membership: WorkspaceMembership = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Page[PossibleDuplicateRead]:
    stmt = select(PossibleDuplicate).where(
        PossibleDuplicate.workspace_id == membership.workspace_id
    )
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
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> PossibleDuplicateRead:
    """Confirm the candidate: link the finding to the candidate exposure."""
    try:
        candidate = resolve_merge(
            db,
            workspace_id=membership.workspace_id,
            possible_duplicate_id=possible_duplicate_id,
            actor=membership.user.username,
        )
    except PossibleDuplicateNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Possible duplicate not found") from exc
    except PossibleDuplicateAlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LinkedFindingMissingError as exc:
        raise HTTPException(
            status_code=404, detail="The finding behind this candidate is gone"
        ) from exc
    return PossibleDuplicateRead.model_validate(candidate)


@router.post("/{possible_duplicate_id}/keep-separate", response_model=PossibleDuplicateRead)
def keep_possible_duplicate_separate(
    possible_duplicate_id: int,
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> PossibleDuplicateRead:
    """Reject the candidate: give the finding its own standalone exposure."""
    try:
        candidate = resolve_keep_separate(
            db,
            workspace_id=membership.workspace_id,
            possible_duplicate_id=possible_duplicate_id,
            actor=membership.user.username,
        )
    except PossibleDuplicateNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Possible duplicate not found") from exc
    except PossibleDuplicateAlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LinkedFindingMissingError as exc:
        raise HTTPException(
            status_code=404, detail="The finding behind this candidate is gone"
        ) from exc
    return PossibleDuplicateRead.model_validate(candidate)
