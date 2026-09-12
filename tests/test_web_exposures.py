"""Tests for the findings queue, its detail panel, and exposure actions."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from webhelpers import bootstrap_workspace, extract_csrf

from threnda.app.models.enums import ExposureHistoryEventType, ExposureStatus, SeverityLevel
from threnda.app.models.exposure import Exposure
from threnda.app.models.workspace import Workspace


def _seed_exposures(db: Session, workspace_id: int) -> tuple[Exposure, Exposure]:
    critical = Exposure(
        workspace_id=workspace_id,
        canonical_fingerprint="web-fp-critical",
        title="Remote code execution in synthetic-app",
        severity=SeverityLevel.CRITICAL,
        risk_score=95,
    )
    medium = Exposure(
        workspace_id=workspace_id,
        canonical_fingerprint="web-fp-medium",
        title="Verbose error messages on synthetic-app",
        severity=SeverityLevel.MEDIUM,
        risk_score=40,
    )
    db.add_all([critical, medium])
    db.commit()
    db.refresh(critical)
    db.refresh(medium)
    return critical, medium


def test_queue_shows_first_run_empty_state_with_no_exposures(client: TestClient) -> None:
    bootstrap_workspace(client)
    response = client.get("/exposures")
    assert response.status_code == 200
    assert "Nothing imported yet" in response.text


def test_queue_lists_seeded_exposures(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    critical, _medium = _seed_exposures(db_session, workspace_id)

    response = client.get("/exposures")
    assert response.status_code == 200
    assert critical.title in response.text
    assert f'id="row-{critical.id}"' in response.text


def test_severity_filter_narrows_results(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    critical, medium = _seed_exposures(db_session, workspace_id)

    response = client.get("/exposures", params={"severity": "critical"})
    assert critical.title in response.text
    assert medium.title not in response.text


def test_search_finds_by_title(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    critical, medium = _seed_exposures(db_session, workspace_id)

    response = client.get("/exposures", params={"q": "verbose"})
    assert medium.title in response.text
    assert critical.title not in response.text


def test_search_with_no_matches_shows_no_results_state(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    _seed_exposures(db_session, workspace_id)

    response = client.get("/exposures", params={"q": "does-not-exist-xyz"})
    assert "No findings match this search" in response.text


def test_htmx_request_returns_a_fragment_not_a_full_page(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    _seed_exposures(db_session, workspace_id)

    response = client.get("/exposures", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "<html" not in response.text.lower()
    assert 'id="queue-inner"' in response.text


def test_detail_panel_shows_evidence_and_identity(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    critical, _medium = _seed_exposures(db_session, workspace_id)

    response = client.get(f"/exposures/{critical.id}/panel?tab=identity")
    assert response.status_code == 200
    assert critical.canonical_fingerprint in response.text


def test_detail_panel_404s_for_a_foreign_workspace_exposure(
    client: TestClient, db_session: Session
) -> None:
    bootstrap_workspace(client, name="ws-a")
    other = Workspace(name="ws-b")
    db_session.add(other)
    db_session.commit()
    foreign = Exposure(
        workspace_id=other.id,
        canonical_fingerprint="foreign-fp",
        title="Should not be visible",
        severity=SeverityLevel.LOW,
    )
    db_session.add(foreign)
    db_session.commit()
    db_session.refresh(foreign)

    response = client.get(f"/exposures/{foreign.id}/panel")
    assert response.status_code == 404


def test_updating_status_is_audited_and_reflected_in_timeline(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    critical, _medium = _seed_exposures(db_session, workspace_id)

    panel = client.get(f"/exposures/{critical.id}/panel?tab=remediation")
    token = extract_csrf(panel.text)

    response = client.post(
        f"/exposures/{critical.id}",
        data={"csrf_token": token, "status": "acknowledged", "tab": "timeline"},
    )
    assert response.status_code == 200
    assert "status changed" in response.text.lower()

    db_session.refresh(critical)
    assert critical.status == ExposureStatus.ACKNOWLEDGED
    history_events = {h.event_type for h in critical.history}
    assert ExposureHistoryEventType.STATUS_CHANGED in history_events


def test_adding_a_note_appends_timeline_entry(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    critical, _medium = _seed_exposures(db_session, workspace_id)

    panel = client.get(f"/exposures/{critical.id}/panel?tab=remediation")
    token = extract_csrf(panel.text)

    response = client.post(
        f"/exposures/{critical.id}",
        data={"csrf_token": token, "note": "Investigating with platform team.", "tab": "timeline"},
    )
    assert response.status_code == 200
    assert "Investigating with platform team." in response.text


def test_owner_and_target_date_updates_persist(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    critical, _medium = _seed_exposures(db_session, workspace_id)

    panel = client.get(f"/exposures/{critical.id}/panel?tab=remediation")
    token = extract_csrf(panel.text)

    client.post(
        f"/exposures/{critical.id}",
        data={"csrf_token": token, "owner": "alice", "tab": "remediation"},
    )
    client.post(
        f"/exposures/{critical.id}",
        data={
            "csrf_token": token,
            "remediation_target_date": "2026-12-01",
            "tab": "remediation",
        },
    )

    db_session.refresh(critical)
    assert critical.owner == "alice"
    assert critical.remediation_target_date is not None
    assert critical.remediation_target_date.isoformat() == "2026-12-01"


def test_update_without_csrf_token_is_rejected(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    critical, _medium = _seed_exposures(db_session, workspace_id)

    response = client.post(f"/exposures/{critical.id}", data={"owner": "mallory"})
    assert response.status_code == 422

    db_session.refresh(critical)
    assert critical.owner is None


def test_update_with_wrong_csrf_token_is_rejected(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    critical, _medium = _seed_exposures(db_session, workspace_id)

    client.get(f"/exposures/{critical.id}/panel?tab=remediation")
    response = client.post(
        f"/exposures/{critical.id}", data={"csrf_token": "totally-wrong", "owner": "mallory"}
    )
    assert response.status_code == 403

    db_session.refresh(critical)
    assert critical.owner is None


def test_bulk_acknowledge_updates_every_selected_exposure(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    critical, medium = _seed_exposures(db_session, workspace_id)

    queue = client.get("/exposures", headers={"HX-Request": "true"})
    token = extract_csrf(queue.text)

    response = client.post(
        "/exposures/bulk",
        data={
            "csrf_token": token,
            "action": "acknowledge",
            "ids": [str(critical.id), str(medium.id)],
            "return_qs": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    db_session.refresh(critical)
    db_session.refresh(medium)
    assert critical.acknowledged is True
    assert medium.acknowledged is True


def test_bulk_update_ignores_ids_from_another_workspace(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client, name="ws-a")
    critical, _medium = _seed_exposures(db_session, workspace_id)
    other = Workspace(name="ws-b")
    db_session.add(other)
    db_session.commit()
    foreign = Exposure(
        workspace_id=other.id,
        canonical_fingerprint="foreign-bulk-fp",
        title="Foreign exposure",
        severity=SeverityLevel.LOW,
    )
    db_session.add(foreign)
    db_session.commit()
    db_session.refresh(foreign)

    queue = client.get("/exposures", headers={"HX-Request": "true"})
    token = extract_csrf(queue.text)

    client.post(
        "/exposures/bulk",
        data={
            "csrf_token": token,
            "action": "in_progress",
            "ids": [str(foreign.id)],
            "return_qs": "",
        },
        follow_redirects=False,
    )

    db_session.refresh(foreign)
    assert foreign.status == ExposureStatus.OPEN
