"""Shared possible-duplicate resolution logic.

Extracted from ``api/v1/possible_duplicates.py`` so the JSON API and the
server-rendered web UI (Milestone 5) resolve a review candidate through
exactly the same code path — see docs/deduplication.md, "The possible-
duplicate review API".
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from threnda.app.models.enums import PossibleDuplicateStatus
from threnda.app.models.exposure import Exposure
from threnda.app.models.finding import Finding
from threnda.app.models.mixins import utcnow
from threnda.app.models.possible_duplicate import PossibleDuplicate
from threnda.app.services.dedup.correlation import (
    create_standalone_exposure,
    link_finding_to_exposure,
)
from threnda.app.services.risk_scoring import recompute_and_persist_risk_score


class PossibleDuplicateNotFoundError(Exception):
    """No pending candidate with this id exists in this workspace."""


class PossibleDuplicateAlreadyResolvedError(Exception):
    """The candidate exists but is no longer pending."""

    def __init__(self, status: PossibleDuplicateStatus) -> None:
        self.status = status
        super().__init__(f"Already resolved as {status.value!r}; nothing left to decide.")


class LinkedFindingMissingError(Exception):
    """The finding behind a pending candidate no longer exists."""


def _get_pending(db: Session, workspace_id: int, possible_duplicate_id: int) -> PossibleDuplicate:
    candidate = db.get(PossibleDuplicate, possible_duplicate_id)
    if candidate is None or candidate.workspace_id != workspace_id:
        raise PossibleDuplicateNotFoundError
    if candidate.status != PossibleDuplicateStatus.PENDING:
        raise PossibleDuplicateAlreadyResolvedError(candidate.status)
    return candidate


def resolve_merge(
    db: Session, *, workspace_id: int, possible_duplicate_id: int, actor: str
) -> PossibleDuplicate:
    """Confirm the candidate: link the finding to the candidate exposure."""
    candidate = _get_pending(db, workspace_id, possible_duplicate_id)
    finding = db.get(Finding, candidate.finding_id)
    if finding is None:
        raise LinkedFindingMissingError

    link_finding_to_exposure(
        db, finding, candidate.candidate_exposure_id, confidence="possible-merged", actor=actor
    )
    exposure = db.get(Exposure, candidate.candidate_exposure_id)
    if exposure is not None:
        recompute_and_persist_risk_score(db, exposure)
    candidate.status = PossibleDuplicateStatus.MERGED
    candidate.resolved_at = utcnow()
    candidate.resolved_by = actor
    db.flush()
    return candidate


def resolve_keep_separate(
    db: Session, *, workspace_id: int, possible_duplicate_id: int, actor: str
) -> PossibleDuplicate:
    """Reject the candidate: give the finding its own standalone exposure."""
    candidate = _get_pending(db, workspace_id, possible_duplicate_id)
    finding = db.get(Finding, candidate.finding_id)
    if finding is None:
        raise LinkedFindingMissingError

    exposure = create_standalone_exposure(db, finding)
    recompute_and_persist_risk_score(db, exposure)
    candidate.status = PossibleDuplicateStatus.KEPT_SEPARATE
    candidate.resolved_at = utcnow()
    candidate.resolved_by = actor
    db.flush()
    return candidate
