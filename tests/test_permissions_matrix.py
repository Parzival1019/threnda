"""Permissions matrix: viewer/analyst/admin/non-member/anonymous, across
every workspace-scoped API route.

Non-member and a workspace that doesn't exist at all must be
indistinguishable (`404`) everywhere; an authenticated member with an
insufficient role gets `403`; a fully anonymous caller gets `401`. See
docs/security.md, "Authorization".
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from conftest import TestUser, grant_membership
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from threnda.app.models.asset import Asset
from threnda.app.models.enums import AssetType, SeverityLevel, SourceType, WorkspaceRole
from threnda.app.models.exposure import Exposure
from threnda.app.models.finding import Finding
from threnda.app.models.source import Source
from threnda.app.models.user import User
from threnda.app.models.webhook_endpoint import WebhookEndpoint
from threnda.app.models.workspace import Workspace
from threnda.app.services.crypto import encrypt_secret
from threnda.app.services.memberships import add_member
from threnda.app.services.user_accounts import create_user


@dataclass
class Matrix:
    workspace_id: int
    admin: TestClient
    analyst: TestClient
    viewer: TestClient
    outsider: TestClient  # authenticated, but not a member of this workspace
    anonymous: TestClient
    exposure_id: int
    source_id: int
    asset_id: int
    webhook_id: int


def _new_logged_in_client(app: FastAPI, username: str, password: str) -> TestClient:
    test_client = TestClient(app)
    test_client.__enter__()
    response = test_client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return test_client


@pytest.fixture
def matrix(
    app: FastAPI,
    client: TestClient,
    admin_user: TestUser,
    other_client: TestClient,
    anonymous_client: TestClient,
    db_session: Session,
) -> Iterator[Matrix]:
    workspace = Workspace(name="matrix-workspace")
    db_session.add(workspace)
    db_session.flush()
    grant_membership(db_session, workspace, admin_user, WorkspaceRole.ADMIN)

    analyst_password = "Analyst-Pass-1234!"  # noqa: S105
    viewer_password = "Viewer-Pass-1234!"  # noqa: S105

    analyst_user = create_user(db_session, "analyst1", analyst_password, actor=None)
    viewer_user = create_user(db_session, "viewer1", viewer_password, actor=None)
    admin_row = db_session.get(User, admin_user.id)
    assert admin_row is not None
    add_member(db_session, workspace, analyst_user, WorkspaceRole.ANALYST, actor=admin_row)
    add_member(db_session, workspace, viewer_user, WorkspaceRole.VIEWER, actor=admin_row)

    source = Source(workspace_id=workspace.id, name="matrix-source", source_type=SourceType.JSON)
    db_session.add(source)
    db_session.flush()
    exposure = Exposure(
        workspace_id=workspace.id,
        canonical_fingerprint="matrix-fp",
        title="Matrix exposure",
        severity=SeverityLevel.HIGH,
    )
    db_session.add(exposure)
    db_session.flush()
    asset = Asset(
        workspace_id=workspace.id,
        canonical_key="matrix-asset",
        name="matrix-asset",
        asset_type=AssetType.HOST,
    )
    db_session.add(asset)
    db_session.flush()
    finding = Finding(
        workspace_id=workspace.id,
        source_id=source.id,
        fingerprint="matrix-fp",
        source_finding_id="matrix-1",
        title="Matrix finding",
        severity=SeverityLevel.HIGH,
        asset_id=asset.id,
        exposure_id=exposure.id,
    )
    db_session.add(finding)
    webhook = WebhookEndpoint(
        workspace_id=workspace.id,
        url="https://example.test/hook",
        secret_ciphertext=encrypt_secret("test-secret", "s3cr3t"),
        enabled=False,
    )
    db_session.add(webhook)
    db_session.commit()

    analyst_client = _new_logged_in_client(app, "analyst1", analyst_password)
    viewer_client = _new_logged_in_client(app, "viewer1", viewer_password)
    try:
        yield Matrix(
            workspace_id=workspace.id,
            admin=client,
            analyst=analyst_client,
            viewer=viewer_client,
            outsider=other_client,
            anonymous=anonymous_client,
            exposure_id=exposure.id,
            source_id=source.id,
            asset_id=asset.id,
            webhook_id=webhook.id,
        )
    finally:
        analyst_client.__exit__(None, None, None)
        viewer_client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Reads: any member (viewer/analyst/admin) may read; a non-member or
# anonymous caller may not.
# ---------------------------------------------------------------------------

READ_ENDPOINTS = [
    "/api/v1/exposures",
    "/api/v1/sources",
    "/api/v1/assets",
    "/api/v1/imports",
    "/api/v1/documents",
    "/api/v1/possible-duplicates",
]


@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_any_member_can_read(matrix: Matrix, path: str) -> None:
    for role_client in (matrix.admin, matrix.analyst, matrix.viewer):
        response = role_client.get(path, params={"workspace_id": matrix.workspace_id})
        assert response.status_code == 200, f"{path} failed for a workspace member"


@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_non_member_gets_404_not_403(matrix: Matrix, path: str) -> None:
    response = matrix.outsider.get(path, params={"workspace_id": matrix.workspace_id})
    assert response.status_code == 404


@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_nonexistent_workspace_also_gets_404(matrix: Matrix, path: str) -> None:
    response = matrix.admin.get(path, params={"workspace_id": 999_999})
    assert response.status_code == 404


@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_anonymous_gets_401(matrix: Matrix, path: str) -> None:
    response = matrix.anonymous.get(path, params={"workspace_id": matrix.workspace_id})
    assert response.status_code == 401


ADMIN_ONLY_READ_ENDPOINTS = ["/api/v1/members", "/api/v1/webhooks", "/api/v1/audit-log"]


@pytest.mark.parametrize("path", ADMIN_ONLY_READ_ENDPOINTS)
def test_only_admin_can_read_admin_surfaces(matrix: Matrix, path: str) -> None:
    assert matrix.admin.get(path, params={"workspace_id": matrix.workspace_id}).status_code == 200
    for role_client in (matrix.analyst, matrix.viewer):
        response = role_client.get(path, params={"workspace_id": matrix.workspace_id})
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Writes: viewer is refused (403); analyst and admin succeed for
# operational writes; only admin succeeds for membership/webhook writes.
# ---------------------------------------------------------------------------


def test_viewer_cannot_patch_exposure(matrix: Matrix) -> None:
    response = matrix.viewer.patch(
        f"/api/v1/exposures/{matrix.exposure_id}",
        params={"workspace_id": matrix.workspace_id},
        json={"acknowledged": True},
    )
    assert response.status_code == 403


def test_analyst_can_patch_exposure(matrix: Matrix) -> None:
    response = matrix.analyst.patch(
        f"/api/v1/exposures/{matrix.exposure_id}",
        params={"workspace_id": matrix.workspace_id},
        json={"acknowledged": True},
    )
    assert response.status_code == 200


def test_admin_can_patch_exposure(matrix: Matrix) -> None:
    response = matrix.admin.patch(
        f"/api/v1/exposures/{matrix.exposure_id}",
        params={"workspace_id": matrix.workspace_id},
        json={"owner": "someone"},
    )
    assert response.status_code == 200


def test_outsider_patching_exposure_gets_404(matrix: Matrix) -> None:
    response = matrix.outsider.patch(
        f"/api/v1/exposures/{matrix.exposure_id}",
        params={"workspace_id": matrix.workspace_id},
        json={"acknowledged": True},
    )
    assert response.status_code == 404


def test_viewer_cannot_create_source(matrix: Matrix) -> None:
    response = matrix.viewer.post(
        "/api/v1/sources",
        params={"workspace_id": matrix.workspace_id},
        json={"name": "nope", "source_type": "json"},
    )
    assert response.status_code == 403


def test_analyst_can_create_source(matrix: Matrix) -> None:
    response = matrix.analyst.post(
        "/api/v1/sources",
        params={"workspace_id": matrix.workspace_id},
        json={"name": "analyst-created", "source_type": "json"},
    )
    assert response.status_code == 201


def test_viewer_cannot_patch_asset(matrix: Matrix) -> None:
    response = matrix.viewer.patch(
        f"/api/v1/assets/{matrix.asset_id}",
        params={"workspace_id": matrix.workspace_id},
        json={"internet_facing": True},
    )
    assert response.status_code == 403


def test_analyst_cannot_manage_members(matrix: Matrix) -> None:
    response = matrix.analyst.post(
        "/api/v1/members",
        params={"workspace_id": matrix.workspace_id},
        json={"username": "should-fail", "password": "Whatever-Pass-1234!", "role": "viewer"},
    )
    assert response.status_code == 403


def test_admin_can_add_a_member(matrix: Matrix) -> None:
    response = matrix.admin.post(
        "/api/v1/members",
        params={"workspace_id": matrix.workspace_id},
        json={"username": "brandnew", "password": "Brandnew-Pass-1234!", "role": "viewer"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "viewer"


def test_analyst_cannot_manage_webhooks(matrix: Matrix) -> None:
    response = matrix.analyst.patch(
        f"/api/v1/webhooks/{matrix.webhook_id}",
        params={"workspace_id": matrix.workspace_id},
        json={"enabled": True},
    )
    assert response.status_code == 403


def test_admin_can_manage_webhooks(matrix: Matrix) -> None:
    response = matrix.admin.patch(
        f"/api/v1/webhooks/{matrix.webhook_id}",
        params={"workspace_id": matrix.workspace_id},
        json={"enabled": True},
    )
    assert response.status_code == 200
    # The secret is never returned, even to an admin, after creation.
    assert "secret" not in response.json()


def test_analyst_can_download_export_but_viewer_cannot(matrix: Matrix) -> None:
    ok = matrix.analyst.get(
        "/api/v1/exports/exposures", params={"workspace_id": matrix.workspace_id}
    )
    assert ok.status_code == 200
    refused = matrix.viewer.get(
        "/api/v1/exports/exposures", params={"workspace_id": matrix.workspace_id}
    )
    assert refused.status_code == 403


def test_idor_cannot_reach_another_workspaces_exposure_via_own_valid_workspace_id(
    matrix: Matrix, other_user: TestUser, db_session: Session
) -> None:
    """The classic IDOR shape: an authenticated member of *some* workspace,
    holding a real exposure id from a *different* one (e.g. guessed by
    incrementing), must not be able to read or write it just because their
    own ``workspace_id`` happens to be valid for them."""
    own_workspace = Workspace(name="outsiders-own-workspace")
    db_session.add(own_workspace)
    db_session.flush()
    grant_membership(db_session, own_workspace, other_user, WorkspaceRole.ADMIN)

    response = matrix.outsider.get(
        f"/api/v1/exposures/{matrix.exposure_id}", params={"workspace_id": own_workspace.id}
    )
    assert response.status_code == 404

    response = matrix.outsider.patch(
        f"/api/v1/exposures/{matrix.exposure_id}",
        params={"workspace_id": own_workspace.id},
        json={"acknowledged": True},
    )
    assert response.status_code == 404


def test_exposure_list_never_leaks_across_workspaces(
    matrix: Matrix, other_user: TestUser, db_session: Session
) -> None:
    own_workspace = Workspace(name="second-workspace-for-listing")
    db_session.add(own_workspace)
    db_session.flush()
    grant_membership(db_session, own_workspace, other_user, WorkspaceRole.ADMIN)

    response = matrix.outsider.get("/api/v1/exposures", params={"workspace_id": own_workspace.id})
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert matrix.exposure_id not in ids


def test_last_admin_cannot_be_demoted_or_removed(matrix: Matrix, db_session: Session) -> None:
    admin_user_row = db_session.query(User).filter_by(username="admin").one()
    response = matrix.admin.patch(
        f"/api/v1/members/{admin_user_row.id}",
        params={"workspace_id": matrix.workspace_id},
        json={"role": "viewer"},
    )
    assert response.status_code == 409

    response = matrix.admin.delete(
        f"/api/v1/members/{admin_user_row.id}", params={"workspace_id": matrix.workspace_id}
    )
    assert response.status_code == 409
