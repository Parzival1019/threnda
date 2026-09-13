"""Tests for the possible-duplicate review API.

A ``PENDING`` candidate is never auto-merged by anything — the point of
this whole review queue is that a human decides. These tests confirm both
outcomes work end to end through the real API and that neither can be
triggered twice.
"""

from __future__ import annotations

from conftest import TestUser, grant_membership
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.enums import SeverityLevel, SourceType
from threnda.app.models.exposure import Exposure
from threnda.app.models.finding import Finding
from threnda.app.models.possible_duplicate import PossibleDuplicate
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace
from threnda.app.services.dedup.correlation import correlate_new_finding


def _make_pending_candidate(db: Session, admin_user: TestUser) -> tuple[int, Exposure, Finding]:
    """A workspace with an existing exposure and a new finding whose title
    is similar enough to fall in the POSSIBLE band (see
    tests/test_dedup_correlation.py for the verified similarity score).
    """
    workspace = Workspace(name="synthetic-review-queue")
    db.add(workspace)
    db.flush()
    grant_membership(db, workspace, admin_user)
    source = Source(workspace_id=workspace.id, name="s", source_type=SourceType.JSON)
    db.add(source)
    db.flush()

    exposure = Exposure(
        workspace_id=workspace.id,
        canonical_fingerprint="fp-1",
        title="SQL injection in login form",
        severity=SeverityLevel.HIGH,
    )
    db.add(exposure)
    db.flush()
    existing_finding = Finding(
        workspace_id=workspace.id,
        source_id=source.id,
        fingerprint="fp-1",
        source_finding_id="existing",
        title="SQL injection in login form",
        severity=SeverityLevel.HIGH,
        cve="CVE-2099-00002",
        exposure_id=exposure.id,
    )
    db.add(existing_finding)
    db.commit()

    new_finding = Finding(
        workspace_id=workspace.id,
        source_id=source.id,
        fingerprint="fp-2",
        source_finding_id="new",
        title="SQL injection vulnerability in the login form",
        severity=SeverityLevel.HIGH,
        cve="CVE-2099-00002",
    )
    db.add(new_finding)
    db.flush()
    correlate_new_finding(db, new_finding)
    db.commit()

    return workspace.id, exposure, new_finding


def _the_only_candidate(db: Session) -> PossibleDuplicate:
    return db.scalars(select(PossibleDuplicate)).one()


def test_pending_candidate_is_listed(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_id, _exposure, _finding = _make_pending_candidate(db_session, admin_user)

    response = client.get("/api/v1/possible-duplicates", params={"workspace_id": workspace_id})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "pending"


def test_merge_links_the_finding_to_the_candidate_exposure(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_id, exposure, finding = _make_pending_candidate(db_session, admin_user)
    candidate_id = _the_only_candidate(db_session).id

    response = client.post(
        f"/api/v1/possible-duplicates/{candidate_id}/merge",
        params={"workspace_id": workspace_id},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "merged"
    assert body["resolved_by"] == admin_user.username

    db_session.refresh(finding)
    assert finding.exposure_id == exposure.id


def test_keep_separate_gives_the_finding_its_own_exposure(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_id, exposure, finding = _make_pending_candidate(db_session, admin_user)
    candidate_id = _the_only_candidate(db_session).id

    response = client.post(
        f"/api/v1/possible-duplicates/{candidate_id}/keep-separate",
        params={"workspace_id": workspace_id},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "kept_separate"

    db_session.refresh(finding)
    assert finding.exposure_id is not None
    assert finding.exposure_id != exposure.id


def test_a_resolved_candidate_cannot_be_acted_on_again(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_id, _exposure, _finding = _make_pending_candidate(db_session, admin_user)
    candidate_id = _the_only_candidate(db_session).id

    first = client.post(
        f"/api/v1/possible-duplicates/{candidate_id}/merge", params={"workspace_id": workspace_id}
    )
    assert first.status_code == 200

    second_merge = client.post(
        f"/api/v1/possible-duplicates/{candidate_id}/merge", params={"workspace_id": workspace_id}
    )
    assert second_merge.status_code == 409

    second_keep_separate = client.post(
        f"/api/v1/possible-duplicates/{candidate_id}/keep-separate",
        params={"workspace_id": workspace_id},
    )
    assert second_keep_separate.status_code == 409


def test_merged_candidates_are_excluded_from_the_default_pending_listing(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_id, _exposure, _finding = _make_pending_candidate(db_session, admin_user)
    candidate_id = _the_only_candidate(db_session).id
    client.post(
        f"/api/v1/possible-duplicates/{candidate_id}/merge", params={"workspace_id": workspace_id}
    )

    default_listing = client.get(
        "/api/v1/possible-duplicates", params={"workspace_id": workspace_id}
    )
    assert default_listing.json()["total"] == 0

    all_listing = client.get(
        "/api/v1/possible-duplicates", params={"workspace_id": workspace_id, "status": "merged"}
    )
    assert all_listing.json()["total"] == 1


def test_wrong_workspace_cannot_act_on_another_workspaces_candidate(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    _workspace_id, _exposure, _finding = _make_pending_candidate(db_session, admin_user)
    other_workspace = Workspace(name="synthetic-other")
    db_session.add(other_workspace)
    db_session.flush()
    grant_membership(db_session, other_workspace, admin_user)
    candidate_id = _the_only_candidate(db_session).id

    response = client.post(
        f"/api/v1/possible-duplicates/{candidate_id}/merge",
        params={"workspace_id": other_workspace.id},
    )

    assert response.status_code == 404


def test_nothing_ever_auto_merges_a_possible_candidate(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    """The candidate must stay PENDING and the finding unlinked until an
    explicit human action — nothing in the correlation path resolves it
    on its own."""
    _workspace_id, _exposure, finding = _make_pending_candidate(db_session, admin_user)

    assert finding.exposure_id is None
    candidate = _the_only_candidate(db_session)
    assert candidate.status.value == "pending"
