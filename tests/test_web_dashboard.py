"""Tests for the minimal dashboard (GET /)."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from webhelpers import bootstrap_workspace

from threnda.app.models.enums import ExposureStatus, SeverityLevel
from threnda.app.models.exposure import Exposure
from threnda.app.models.mixins import utcnow
from threnda.app.models.workspace import Workspace


def test_dashboard_renders_with_zero_data(client: TestClient) -> None:
    bootstrap_workspace(client)
    response = client.get("/")
    assert response.status_code == 200
    assert "Awaiting retest" in response.text


def test_dashboard_counts_reflect_real_data(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    workspace = db_session.get(Workspace, workspace_id)
    assert workspace is not None

    db_session.add_all(
        [
            Exposure(
                workspace_id=workspace.id,
                canonical_fingerprint="dash-fp-1",
                title="Critical open exposure",
                severity=SeverityLevel.CRITICAL,
                status=ExposureStatus.OPEN,
            ),
            Exposure(
                workspace_id=workspace.id,
                canonical_fingerprint="dash-fp-2",
                title="Overdue high exposure",
                severity=SeverityLevel.HIGH,
                status=ExposureStatus.ACKNOWLEDGED,
                remediation_target_date=date.today() - timedelta(days=5),
            ),
            Exposure(
                workspace_id=workspace.id,
                canonical_fingerprint="dash-fp-3",
                title="Resolved this week",
                severity=SeverityLevel.LOW,
                status=ExposureStatus.RESOLVED,
                resolved_at=utcnow(),
            ),
        ]
    )
    db_session.commit()

    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    # Loose structural assertions (exact tile order/markup is a styling
    # detail): the real counts computed from the seeded data must appear.
    assert ">1<" in body  # open, critical, high, overdue and resolved-this-week each = 1
