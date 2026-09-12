"""Unit tests for the extracted possible-duplicate resolution service.

Complements ``tests/test_api_possible_duplicates.py`` (the HTTP contract);
these exercise the same logic directly since Milestone 5's web UI calls it
without going through that endpoint.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from threnda.app.models.enums import PossibleDuplicateStatus, SeverityLevel, SourceType
from threnda.app.models.exposure import Exposure
from threnda.app.models.finding import Finding
from threnda.app.models.possible_duplicate import PossibleDuplicate
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace
from threnda.app.services.possible_duplicates import (
    PossibleDuplicateAlreadyResolvedError,
    PossibleDuplicateNotFoundError,
    resolve_keep_separate,
    resolve_merge,
)


def _seed_candidate(db: Session) -> tuple[Workspace, PossibleDuplicate, Exposure, Finding]:
    workspace = Workspace(name="synthetic-review-service")
    db.add(workspace)
    db.flush()
    source = Source(workspace_id=workspace.id, name="synthetic-source", source_type=SourceType.JSON)
    db.add(source)
    db.flush()
    exposure = Exposure(
        workspace_id=workspace.id,
        canonical_fingerprint="review-fp-1",
        title="Existing exposure",
        severity=SeverityLevel.MEDIUM,
    )
    db.add(exposure)
    db.flush()
    finding = Finding(
        workspace_id=workspace.id,
        source_id=source.id,
        fingerprint="review-fp-2",
        source_finding_id="candidate-1",
        title="Candidate finding",
        severity=SeverityLevel.MEDIUM,
    )
    db.add(finding)
    db.flush()
    candidate = PossibleDuplicate(
        workspace_id=workspace.id,
        finding_id=finding.id,
        candidate_exposure_id=exposure.id,
        similarity_score=0.8,
    )
    db.add(candidate)
    db.flush()
    return workspace, candidate, exposure, finding


def test_resolve_merge_links_finding_and_marks_merged(db_session: Session) -> None:
    workspace, candidate, exposure, finding = _seed_candidate(db_session)

    resolved = resolve_merge(
        db_session, workspace_id=workspace.id, possible_duplicate_id=candidate.id, actor="tester"
    )

    assert resolved.status == PossibleDuplicateStatus.MERGED
    assert resolved.resolved_by == "tester"
    assert finding.exposure_id == exposure.id


def test_resolve_keep_separate_creates_standalone_exposure(db_session: Session) -> None:
    workspace, candidate, exposure, finding = _seed_candidate(db_session)

    resolved = resolve_keep_separate(
        db_session, workspace_id=workspace.id, possible_duplicate_id=candidate.id, actor="tester"
    )

    assert resolved.status == PossibleDuplicateStatus.KEPT_SEPARATE
    assert finding.exposure_id is not None
    assert finding.exposure_id != exposure.id


def test_resolve_merge_twice_raises_already_resolved(db_session: Session) -> None:
    workspace, candidate, _exposure, _finding = _seed_candidate(db_session)
    resolve_merge(
        db_session, workspace_id=workspace.id, possible_duplicate_id=candidate.id, actor="tester"
    )

    with pytest.raises(PossibleDuplicateAlreadyResolvedError):
        resolve_merge(
            db_session,
            workspace_id=workspace.id,
            possible_duplicate_id=candidate.id,
            actor="tester",
        )


def test_unknown_candidate_raises_not_found(db_session: Session) -> None:
    workspace = Workspace(name="synthetic-review-empty")
    db_session.add(workspace)
    db_session.flush()

    with pytest.raises(PossibleDuplicateNotFoundError):
        resolve_merge(
            db_session, workspace_id=workspace.id, possible_duplicate_id=999999, actor="tester"
        )


def test_candidate_from_another_workspace_raises_not_found(db_session: Session) -> None:
    _workspace, candidate, _exposure, _finding = _seed_candidate(db_session)
    other_workspace = Workspace(name="synthetic-review-other")
    db_session.add(other_workspace)
    db_session.flush()

    with pytest.raises(PossibleDuplicateNotFoundError):
        resolve_merge(
            db_session,
            workspace_id=other_workspace.id,
            possible_duplicate_id=candidate.id,
            actor="tester",
        )
