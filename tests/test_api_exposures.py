"""Tests for GET/PATCH /api/v1/exposures — synthetic fixtures only."""

from __future__ import annotations

from conftest import TestUser, grant_membership
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from threnda.app.models.enums import SeverityLevel
from threnda.app.models.exposure import Exposure
from threnda.app.models.workspace import Workspace


def _seed_exposures(
    db_session: Session, admin_user: TestUser
) -> tuple[Workspace, Workspace, Exposure]:
    workspace_a = Workspace(name="workspace-a")
    workspace_b = Workspace(name="workspace-b")
    db_session.add_all([workspace_a, workspace_b])
    db_session.flush()
    grant_membership(db_session, workspace_a, admin_user)
    grant_membership(db_session, workspace_b, admin_user)

    exposures = [
        Exposure(
            workspace_id=workspace_a.id,
            canonical_fingerprint="exp-fp-critical",
            title="Remote code execution in synthetic-app",
            severity=SeverityLevel.CRITICAL,
            risk_score=95,
            owner="alice",
        ),
        Exposure(
            workspace_id=workspace_a.id,
            canonical_fingerprint="exp-fp-medium",
            title="Verbose error messages on synthetic-app",
            severity=SeverityLevel.MEDIUM,
            risk_score=40,
        ),
        Exposure(
            workspace_id=workspace_b.id,
            canonical_fingerprint="exp-fp-other-workspace",
            title="Should never appear in workspace-a queries",
            severity=SeverityLevel.HIGH,
            risk_score=80,
        ),
    ]
    db_session.add_all(exposures)
    db_session.commit()
    for exposure in exposures:
        db_session.refresh(exposure)
    return workspace_a, workspace_b, exposures[0]


def test_list_exposures_is_scoped_to_workspace(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_a, workspace_b, _ = _seed_exposures(db_session, admin_user)

    response = client.get("/api/v1/exposures", params={"workspace_id": workspace_a.id})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    titles = {item["title"] for item in body["items"]}
    assert "Should never appear in workspace-a queries" not in titles

    response_b = client.get("/api/v1/exposures", params={"workspace_id": workspace_b.id})
    assert response_b.json()["total"] == 1


def test_list_exposures_orders_by_risk_score_descending(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_a, _, _ = _seed_exposures(db_session, admin_user)

    response = client.get("/api/v1/exposures", params={"workspace_id": workspace_a.id})
    scores = [item["risk_score"] for item in response.json()["items"]]
    assert scores == sorted(scores, reverse=True)


def test_list_exposures_filters_by_severity_and_search(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_a, _, _ = _seed_exposures(db_session, admin_user)

    response = client.get(
        "/api/v1/exposures",
        params={"workspace_id": workspace_a.id, "severity": "critical"},
    )
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["severity"] == "critical"

    response = client.get(
        "/api/v1/exposures",
        params={"workspace_id": workspace_a.id, "q": "verbose"},
    )
    assert response.json()["total"] == 1


def test_list_exposures_pagination(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_a, _, _ = _seed_exposures(db_session, admin_user)

    response = client.get(
        "/api/v1/exposures",
        params={"workspace_id": workspace_a.id, "limit": 1, "offset": 0},
    )
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["limit"] == 1
    assert body["offset"] == 0


def test_get_exposure_detail(client: TestClient, db_session: Session, admin_user: TestUser) -> None:
    workspace_a, _, exposure = _seed_exposures(db_session, admin_user)

    response = client.get(
        f"/api/v1/exposures/{exposure.id}", params={"workspace_id": workspace_a.id}
    )
    assert response.status_code == 200
    assert response.json()["owner"] == "alice"


def test_get_exposure_wrong_workspace_returns_404(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    _, workspace_b, exposure = _seed_exposures(db_session, admin_user)

    response = client.get(
        f"/api/v1/exposures/{exposure.id}", params={"workspace_id": workspace_b.id}
    )
    assert response.status_code == 404


def test_get_exposure_missing_returns_404(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_a, _, _ = _seed_exposures(db_session, admin_user)

    response = client.get("/api/v1/exposures/999999", params={"workspace_id": workspace_a.id})
    assert response.status_code == 404


def test_patch_exposure_requires_workspace_id(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    _, _, exposure = _seed_exposures(db_session, admin_user)

    response = client.patch(f"/api/v1/exposures/{exposure.id}", json={"owner": "bob"})
    assert response.status_code == 422


def test_patch_exposure_changes_status_and_records_history(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_a, _, exposure = _seed_exposures(db_session, admin_user)

    response = client.patch(
        f"/api/v1/exposures/{exposure.id}",
        params={"workspace_id": workspace_a.id},
        json={"status": "acknowledged"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "acknowledged"

    history_response = client.get(
        f"/api/v1/exposures/{exposure.id}", params={"workspace_id": workspace_a.id}
    )
    assert history_response.status_code == 200

    db_session.refresh(exposure)
    assert len(exposure.history) == 1
    assert exposure.history[0].event_type.value == "status_changed"
    assert exposure.history[0].previous_value == "open"
    assert exposure.history[0].new_value == "acknowledged"
    assert exposure.history[0].actor == admin_user.username


def test_patch_exposure_resolve_sets_resolved_at_and_reopen_clears_it(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_a, _, exposure = _seed_exposures(db_session, admin_user)

    resolved = client.patch(
        f"/api/v1/exposures/{exposure.id}",
        params={"workspace_id": workspace_a.id},
        json={"status": "resolved"},
    )
    assert resolved.json()["resolved_at"] is not None

    reopened = client.patch(
        f"/api/v1/exposures/{exposure.id}",
        params={"workspace_id": workspace_a.id},
        json={"status": "open"},
    )
    assert reopened.json()["resolved_at"] is None

    db_session.refresh(exposure)
    event_types = [h.event_type.value for h in exposure.history]
    assert event_types == ["resolved", "reopened"]


def test_patch_exposure_owner_and_note_and_target_date(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_a, _, exposure = _seed_exposures(db_session, admin_user)

    response = client.patch(
        f"/api/v1/exposures/{exposure.id}",
        params={"workspace_id": workspace_a.id},
        json={
            "owner": "carol",
            "remediation_target_date": "2099-06-01",
            "note": "Escalated to platform team.",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["owner"] == "carol"
    assert body["remediation_target_date"] == "2099-06-01"

    db_session.refresh(exposure)
    event_types = {h.event_type.value for h in exposure.history}
    assert event_types == {"owner_changed", "remediation_date_changed", "note_added"}


def test_patch_exposure_unknown_field_is_rejected(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_a, _, exposure = _seed_exposures(db_session, admin_user)

    response = client.patch(
        f"/api/v1/exposures/{exposure.id}",
        params={"workspace_id": workspace_a.id},
        json={"risk_score": 100},
    )
    assert response.status_code == 422


def test_patch_exposure_wrong_workspace_returns_404(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    _, workspace_b, exposure = _seed_exposures(db_session, admin_user)

    response = client.patch(
        f"/api/v1/exposures/{exposure.id}",
        params={"workspace_id": workspace_b.id},
        json={"owner": "mallory"},
    )
    assert response.status_code == 404


def test_patch_exposure_invalid_transition_returns_409_and_does_not_mutate(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_a, _, exposure = _seed_exposures(db_session, admin_user)
    # ACKNOWLEDGED -> OPEN is not a documented edge (only RESOLVED/
    # RISK_ACCEPTED reopen to OPEN) — see threnda.app.services.exposures.
    client.patch(
        f"/api/v1/exposures/{exposure.id}",
        params={"workspace_id": workspace_a.id},
        json={"status": "acknowledged"},
    )

    response = client.patch(
        f"/api/v1/exposures/{exposure.id}",
        params={"workspace_id": workspace_a.id},
        json={"status": "open"},
    )
    assert response.status_code == 409

    db_session.refresh(exposure)
    assert exposure.status.value == "acknowledged"


def test_get_exposure_includes_risk_score_breakdown(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_a, _, exposure = _seed_exposures(db_session, admin_user)

    response = client.get(
        f"/api/v1/exposures/{exposure.id}", params={"workspace_id": workspace_a.id}
    )
    assert response.status_code == 200
    body = response.json()
    assert "risk_score_breakdown" in body
    components = body["risk_score_breakdown"]
    assert components, "breakdown must not be empty"
    for component in components:
        assert set(component) == {"label", "points", "detail"}
    labels = {c["label"] for c in components}
    assert "Severity" in labels
    raw_total = sum(c["points"] for c in components)
    assert body["risk_score"] == max(0, min(100, raw_total))


def test_list_exposures_filters_by_risk_range(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace_a, _, _ = _seed_exposures(db_session, admin_user)

    response = client.get(
        "/api/v1/exposures",
        params={"workspace_id": workspace_a.id, "min_risk_score": 90},
    )
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["risk_score"] == 95

    response = client.get(
        "/api/v1/exposures",
        params={"workspace_id": workspace_a.id, "max_risk_score": 50},
    )
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["risk_score"] == 40
