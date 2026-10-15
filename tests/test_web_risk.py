"""Web-UI regression tests for Milestone 4: the Risk tab, lifecycle
transition enforcement in the detail panel, and the row/queue sync those
actions trigger.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from webhelpers import bootstrap_workspace, extract_csrf

from threnda.app.models.asset import Asset
from threnda.app.models.enums import SeverityLevel, SourceType
from threnda.app.models.exposure import Exposure
from threnda.app.models.finding import Finding
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace


def _seed_exposure_with_asset(db: Session, workspace_id: int) -> tuple[Exposure, Asset]:
    source = Source(workspace_id=workspace_id, name="s", source_type=SourceType.JSON)
    asset = Asset(workspace_id=workspace_id, canonical_key="host:a", name="synthetic-host")
    db.add_all([source, asset])
    db.flush()
    exposure = Exposure(
        workspace_id=workspace_id,
        canonical_fingerprint="fp-risk",
        title="Synthetic risk exposure",
        severity=SeverityLevel.MEDIUM,
    )
    db.add(exposure)
    db.flush()
    finding = Finding(
        workspace_id=workspace_id,
        source_id=source.id,
        asset_id=asset.id,
        exposure_id=exposure.id,
        fingerprint="fp-risk",
        source_finding_id="f1",
        title="Synthetic finding",
        severity=SeverityLevel.MEDIUM,
    )
    db.add(finding)
    db.commit()
    db.refresh(exposure)
    db.refresh(asset)
    return exposure, asset


def test_queue_shows_a_risk_column(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    _seed_exposure_with_asset(db_session, workspace_id)

    response = client.get("/exposures")
    assert response.status_code == 200
    assert ">Risk<" in response.text
    assert 'class="risk-value' in response.text


def test_risk_tab_shows_the_breakdown(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    exposure, _asset = _seed_exposure_with_asset(db_session, workspace_id)

    response = client.get(f"/exposures/{exposure.id}/panel?tab=risk")
    assert response.status_code == 200
    assert "Score breakdown" in response.text
    assert "Severity" in response.text


def test_editing_linked_asset_updates_score_and_syncs_the_row(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    exposure, asset = _seed_exposure_with_asset(db_session, workspace_id)
    before_score = exposure.risk_score

    panel = client.get(f"/exposures/{exposure.id}/panel?tab=risk")
    token = extract_csrf(panel.text)
    assert f"/assets/{asset.id}?workspace_id=" in panel.text

    response = client.post(
        f"/assets/{asset.id}",
        data={
            "csrf_token": token,
            "exposure_id": str(exposure.id),
            "business_criticality": "critical",
            "internet_facing": "true",
            "tab": "risk",
        },
    )
    assert response.status_code == 200
    assert 'hx-swap-oob="true"' in response.text
    assert f'id="row-{exposure.id}"' in response.text

    db_session.refresh(exposure)
    assert exposure.risk_score > before_score

    row_match = re.search(
        rf'id="row-{exposure.id}"[^>]*hx-swap-oob="true".*?risk-value[^>]*>(\d+)<',
        response.text,
        re.DOTALL,
    )
    assert row_match is not None
    assert int(row_match.group(1)) == exposure.risk_score


def test_asset_edit_for_a_foreign_workspace_asset_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client, name="ws-a")
    exposure, _asset = _seed_exposure_with_asset(db_session, workspace_id)

    other = Workspace(name="ws-b")
    db_session.add(other)
    db_session.commit()
    other_source = Source(workspace_id=other.id, name="s2", source_type=SourceType.JSON)
    other_asset = Asset(workspace_id=other.id, canonical_key="host:b", name="b")
    db_session.add_all([other_source, other_asset])
    db_session.commit()

    panel = client.get(f"/exposures/{exposure.id}/panel?tab=risk")
    token = extract_csrf(panel.text)

    response = client.post(
        f"/assets/{other_asset.id}",
        data={
            "csrf_token": token,
            "exposure_id": str(exposure.id),
            "business_criticality": "critical",
            "tab": "risk",
        },
    )
    assert response.status_code == 404


def test_status_select_only_offers_allowed_transitions(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    exposure, _asset = _seed_exposure_with_asset(db_session, workspace_id)

    panel = client.get(f"/exposures/{exposure.id}/panel?tab=remediation")
    token = extract_csrf(panel.text)
    client.post(
        f"/exposures/{exposure.id}",
        data={"csrf_token": token, "status": "risk_accepted", "tab": "remediation"},
    )

    panel_after = client.get(f"/exposures/{exposure.id}/panel?tab=remediation")
    status_select = re.search(
        r'<select id="status-select".*?</select>', panel_after.text, flags=re.DOTALL
    )
    assert status_select is not None
    options = re.findall(r'<option value="([a-z_]+)"', status_select.group(0))
    # RISK_ACCEPTED's only allowed next status is OPEN — the select must not
    # offer acknowledged/in_progress/fix_reported, and never "resolved"
    # (verified closure goes through the retest form).
    assert set(options) == {"risk_accepted", "open"}


def test_invalid_transition_via_the_panel_shows_an_error_not_a_500(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    exposure, _asset = _seed_exposure_with_asset(db_session, workspace_id)

    panel = client.get(f"/exposures/{exposure.id}/panel?tab=remediation")
    token = extract_csrf(panel.text)
    client.post(
        f"/exposures/{exposure.id}",
        data={"csrf_token": token, "status": "acknowledged", "tab": "remediation"},
    )

    panel2 = client.get(f"/exposures/{exposure.id}/panel?tab=remediation")
    token2 = extract_csrf(panel2.text)
    # ACKNOWLEDGED -> OPEN is not a documented edge (reopen is only from a
    # closed state) — this must come back as a rendered error, not a crash.
    response = client.post(
        f"/exposures/{exposure.id}",
        data={"csrf_token": token2, "status": "open", "tab": "remediation"},
    )
    assert response.status_code == 200
    assert "error-banner" in response.text

    db_session.refresh(exposure)
    assert exposure.status.value == "acknowledged"


def test_bulk_action_skips_an_exposure_it_cannot_transition(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    exposure, _asset = _seed_exposure_with_asset(db_session, workspace_id)

    # Move it to a closed state first so "mark in progress" cannot apply.
    panel = client.get(f"/exposures/{exposure.id}/panel?tab=remediation")
    token = extract_csrf(panel.text)
    client.post(
        f"/exposures/{exposure.id}",
        data={"csrf_token": token, "status": "risk_accepted", "tab": "remediation"},
    )

    queue = client.get("/exposures", headers={"HX-Request": "true"})
    bulk_token = extract_csrf(queue.text)
    response = client.post(
        "/exposures/bulk",
        data={
            "csrf_token": bulk_token,
            "action": "in_progress",
            "ids": [str(exposure.id)],
            "return_qs": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303  # the batch itself still succeeds

    db_session.refresh(exposure)
    assert exposure.status.value == "risk_accepted"  # untouched, not crashed


def test_risk_range_filter_narrows_the_queue(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    exposure, _asset = _seed_exposure_with_asset(db_session, workspace_id)
    db_session.refresh(exposure)

    too_high = client.get("/exposures", params={"min_risk": exposure.risk_score + 1})
    assert f'id="row-{exposure.id}"' not in too_high.text

    inclusive = client.get("/exposures", params={"min_risk": exposure.risk_score})
    assert f'id="row-{exposure.id}"' in inclusive.text
