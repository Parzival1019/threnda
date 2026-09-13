"""Tests for PATCH /api/v1/assets/{id} — the Milestone 4 manual risk-scoring
update, and its ripple into every exposure the asset's findings are linked
to.
"""

from __future__ import annotations

from conftest import TestUser, grant_membership
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from threnda.app.models.asset import Asset
from threnda.app.models.enums import AssetCriticality, AssetEnvironment, SeverityLevel, SourceType
from threnda.app.models.exposure import Exposure
from threnda.app.models.finding import Finding
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace


def _seed(db: Session, admin_user: TestUser) -> tuple[Workspace, Asset, Exposure]:
    workspace = Workspace(name="synthetic-assets")
    db.add(workspace)
    db.flush()
    grant_membership(db, workspace, admin_user)
    source = Source(workspace_id=workspace.id, name="s", source_type=SourceType.JSON)
    asset = Asset(
        workspace_id=workspace.id,
        canonical_key="host:a",
        name="a",
        environment=AssetEnvironment.PRODUCTION,
    )
    db.add_all([source, asset])
    db.flush()
    exposure = Exposure(
        workspace_id=workspace.id,
        canonical_fingerprint="fp",
        title="Synthetic exposure",
        severity=SeverityLevel.MEDIUM,
    )
    db.add(exposure)
    db.flush()
    finding = Finding(
        workspace_id=workspace.id,
        source_id=source.id,
        asset_id=asset.id,
        exposure_id=exposure.id,
        fingerprint="fp",
        source_finding_id="s1",
        title="Synthetic finding",
        severity=SeverityLevel.MEDIUM,
    )
    db.add(finding)
    db.commit()
    db.refresh(asset)
    db.refresh(exposure)
    return workspace, asset, exposure


def test_patch_asset_sets_business_criticality_and_recomputes_exposure(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace, asset, exposure = _seed(db_session, admin_user)
    before = exposure.risk_score

    response = client.patch(
        f"/api/v1/assets/{asset.id}",
        params={"workspace_id": workspace.id},
        json={"business_criticality": "critical"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["business_criticality"] == "critical"

    db_session.refresh(exposure)
    assert exposure.risk_score > before


def test_patch_asset_internet_facing_true_then_reset_to_null(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace, asset, _exposure = _seed(db_session, admin_user)

    response = client.patch(
        f"/api/v1/assets/{asset.id}",
        params={"workspace_id": workspace.id},
        json={"internet_facing": True},
    )
    assert response.status_code == 200
    assert response.json()["internet_facing"] is True

    # Explicit null legitimately means "back to unknown" for this nullable
    # field — unlike business_criticality, which cannot be nulled.
    response = client.patch(
        f"/api/v1/assets/{asset.id}",
        params={"workspace_id": workspace.id},
        json={"internet_facing": None},
    )
    assert response.status_code == 200
    assert response.json()["internet_facing"] is None


def test_patch_asset_rejects_explicit_null_criticality(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace, asset, _exposure = _seed(db_session, admin_user)

    response = client.patch(
        f"/api/v1/assets/{asset.id}",
        params={"workspace_id": workspace.id},
        json={"business_criticality": None},
    )
    assert response.status_code == 422


def test_patch_asset_wrong_workspace_returns_404(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    _workspace, asset, _exposure = _seed(db_session, admin_user)
    other = Workspace(name="synthetic-other")
    db_session.add(other)
    db_session.commit()

    response = client.patch(
        f"/api/v1/assets/{asset.id}",
        params={"workspace_id": other.id},
        json={"business_criticality": "high"},
    )
    assert response.status_code == 404


def test_patch_asset_missing_returns_404(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace, _asset, _exposure = _seed(db_session, admin_user)

    response = client.patch(
        "/api/v1/assets/999999",
        params={"workspace_id": workspace.id},
        json={"business_criticality": "high"},
    )
    assert response.status_code == 404


def test_patch_asset_unknown_field_is_rejected(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace, asset, _exposure = _seed(db_session, admin_user)

    response = client.patch(
        f"/api/v1/assets/{asset.id}",
        params={"workspace_id": workspace.id},
        json={"name": "renamed"},
    )
    assert response.status_code == 422


def test_patch_asset_with_no_changes_does_not_touch_exposure_score(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace, asset, exposure = _seed(db_session, admin_user)
    before = exposure.risk_score

    response = client.patch(
        f"/api/v1/assets/{asset.id}",
        params={"workspace_id": workspace.id},
        json={},
    )
    assert response.status_code == 200

    db_session.refresh(exposure)
    assert exposure.risk_score == before


def test_patch_asset_invalid_criticality_value_returns_422(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace, asset, _exposure = _seed(db_session, admin_user)

    response = client.patch(
        f"/api/v1/assets/{asset.id}",
        params={"workspace_id": workspace.id},
        json={"business_criticality": "not-a-real-value"},
    )
    assert response.status_code == 422


def test_list_assets_includes_new_risk_fields(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace, asset, _exposure = _seed(db_session, admin_user)

    response = client.get("/api/v1/assets", params={"workspace_id": workspace.id})
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert "internet_facing" in item
    assert item["business_criticality"] == AssetCriticality.UNKNOWN.value
