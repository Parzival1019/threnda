"""Tests for the possible-duplicate review queue web routes."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from webhelpers import bootstrap_workspace, extract_csrf

from threnda.app.models.enums import PossibleDuplicateStatus, SeverityLevel, SourceType
from threnda.app.models.exposure import Exposure
from threnda.app.models.finding import Finding
from threnda.app.models.possible_duplicate import PossibleDuplicate
from threnda.app.models.source import Source


def _seed_candidate(db: Session, workspace_id: int) -> tuple[PossibleDuplicate, Exposure, Finding]:
    source = Source(workspace_id=workspace_id, name="s1", source_type=SourceType.JSON)
    db.add(source)
    db.flush()
    exposure = Exposure(
        workspace_id=workspace_id,
        canonical_fingerprint="review-web-fp",
        title="Existing exposure",
        severity=SeverityLevel.MEDIUM,
    )
    db.add(exposure)
    db.flush()
    finding = Finding(
        workspace_id=workspace_id,
        source_id=source.id,
        fingerprint="review-web-fp-2",
        source_finding_id="candidate-web-1",
        title="Candidate finding",
        severity=SeverityLevel.MEDIUM,
    )
    db.add(finding)
    db.flush()
    candidate = PossibleDuplicate(
        workspace_id=workspace_id,
        finding_id=finding.id,
        candidate_exposure_id=exposure.id,
        similarity_score=0.8,
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate, exposure, finding


def test_review_queue_shows_empty_state(client: TestClient) -> None:
    bootstrap_workspace(client)
    response = client.get("/review")
    assert response.status_code == 200
    assert "Nothing to review" in response.text


def test_review_queue_lists_pending_candidates(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    candidate, exposure, finding = _seed_candidate(db_session, workspace_id)

    response = client.get("/review")
    assert response.status_code == 200
    assert finding.title in response.text
    assert exposure.title in response.text


def test_merge_links_the_finding_and_removes_it_from_the_queue(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    candidate, exposure, finding = _seed_candidate(db_session, workspace_id)

    page = client.get("/review")
    token = extract_csrf(page.text)
    response = client.post(
        f"/review/{candidate.id}/merge", data={"csrf_token": token}, follow_redirects=False
    )
    assert response.status_code == 303

    db_session.refresh(candidate)
    db_session.refresh(finding)
    assert candidate.status == PossibleDuplicateStatus.MERGED
    assert finding.exposure_id == exposure.id

    after = client.get("/review")
    assert "Nothing to review" in after.text


def test_keep_separate_creates_a_standalone_exposure(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    candidate, exposure, finding = _seed_candidate(db_session, workspace_id)

    page = client.get("/review")
    token = extract_csrf(page.text)
    response = client.post(
        f"/review/{candidate.id}/keep-separate",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303

    db_session.refresh(candidate)
    db_session.refresh(finding)
    assert candidate.status == PossibleDuplicateStatus.KEPT_SEPARATE
    assert finding.exposure_id is not None
    assert finding.exposure_id != exposure.id


def test_resolving_twice_returns_409(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    candidate, _exposure, _finding = _seed_candidate(db_session, workspace_id)

    page = client.get("/review")
    token = extract_csrf(page.text)
    client.post(f"/review/{candidate.id}/merge", data={"csrf_token": token})

    response = client.post(f"/review/{candidate.id}/merge", data={"csrf_token": token})
    assert response.status_code == 409
