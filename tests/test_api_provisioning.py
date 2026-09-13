"""Tests for POST /api/v1/workspaces and POST /api/v1/sources.

Added in Milestone 2 so a workspace and a source can be provisioned through
the documented HTTP API before uploading anything — no undocumented SQL
shell step required (see the Milestone 2 acceptance checklist).
"""

from __future__ import annotations

from conftest import TestUser, grant_membership
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from threnda.app.models.workspace import Workspace


def test_create_workspace(client: TestClient) -> None:
    response = client.post("/api/v1/workspaces", json={"name": "synthetic-workspace"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "synthetic-workspace"
    assert "id" in body


def test_create_workspace_rejects_duplicate_name(client: TestClient) -> None:
    client.post("/api/v1/workspaces", json={"name": "dup"})

    response = client.post("/api/v1/workspaces", json={"name": "dup"})

    assert response.status_code == 409


def test_create_workspace_rejects_empty_name(client: TestClient) -> None:
    response = client.post("/api/v1/workspaces", json={"name": ""})

    assert response.status_code == 422


def test_list_workspaces_after_creation(client: TestClient) -> None:
    client.post("/api/v1/workspaces", json={"name": "listed-workspace"})

    response = client.get("/api/v1/workspaces")

    assert response.status_code == 200
    names = {item["name"] for item in response.json()["items"]}
    assert "listed-workspace" in names


def test_create_source_for_each_supported_format(client: TestClient) -> None:
    workspace_id = client.post("/api/v1/workspaces", json={"name": "formats"}).json()["id"]

    for source_type in ("csv", "json", "sarif"):
        response = client.post(
            "/api/v1/sources",
            params={"workspace_id": workspace_id},
            json={"name": f"{source_type}-source", "source_type": source_type},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["source_type"] == source_type
        assert body["workspace_id"] == workspace_id


def test_create_source_rejects_unknown_workspace(client: TestClient) -> None:
    response = client.post(
        "/api/v1/sources",
        params={"workspace_id": 999999},
        json={"name": "orphan", "source_type": "csv"},
    )

    assert response.status_code == 404


def test_created_source_is_visible_via_list(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    workspace = Workspace(name="visible-check")
    db_session.add(workspace)
    db_session.flush()
    grant_membership(db_session, workspace, admin_user)

    create_response = client.post(
        "/api/v1/sources",
        params={"workspace_id": workspace.id},
        json={"name": "visible-source", "source_type": "json"},
    )
    assert create_response.status_code == 201

    list_response = client.get("/api/v1/sources", params={"workspace_id": workspace.id})
    assert list_response.json()["total"] == 1
