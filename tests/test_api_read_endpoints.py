"""Tests for the read-only /sources, /imports and /assets endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from threnda.app.models.asset import Asset
from threnda.app.models.enums import AssetEnvironment, AssetType, ImportStatus, SourceType
from threnda.app.models.import_run import ImportRun
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace


def test_list_sources_scoped_and_paginated(client: TestClient, db_session: Session) -> None:
    workspace_a = Workspace(name="workspace-a")
    workspace_b = Workspace(name="workspace-b")
    db_session.add_all([workspace_a, workspace_b])
    db_session.flush()

    db_session.add_all(
        [
            Source(workspace_id=workspace_a.id, name="CSV export 1", source_type=SourceType.CSV),
            Source(workspace_id=workspace_a.id, name="SARIF export", source_type=SourceType.SARIF),
            Source(workspace_id=workspace_b.id, name="Other workspace", source_type=SourceType.CSV),
        ]
    )
    db_session.commit()

    response = client.get("/api/v1/sources", params={"workspace_id": workspace_a.id})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["name"] for item in body["items"]} == {"CSV export 1", "SARIF export"}
    assert all("meta" in item for item in body["items"])


def test_list_imports_filters_by_source(client: TestClient, db_session: Session) -> None:
    workspace = Workspace(name="default")
    db_session.add(workspace)
    db_session.flush()

    source_1 = Source(workspace_id=workspace.id, name="Source 1", source_type=SourceType.CSV)
    source_2 = Source(workspace_id=workspace.id, name="Source 2", source_type=SourceType.JSON)
    db_session.add_all([source_1, source_2])
    db_session.flush()

    db_session.add_all(
        [
            ImportRun(
                workspace_id=workspace.id,
                source_id=source_1.id,
                filename="synthetic-export-1.csv",
                file_hash="a" * 64,
                status=ImportStatus.COMPLETED,
                total_records=10,
                successful_records=10,
            ),
            ImportRun(
                workspace_id=workspace.id,
                source_id=source_2.id,
                filename="synthetic-export.json",
                file_hash="b" * 64,
                status=ImportStatus.FAILED,
                total_records=5,
                failed_records=5,
            ),
        ]
    )
    db_session.commit()

    response = client.get("/api/v1/imports", params={"workspace_id": workspace.id})
    assert response.json()["total"] == 2

    filtered = client.get(
        "/api/v1/imports",
        params={"workspace_id": workspace.id, "source_id": source_1.id},
    )
    body = filtered.json()
    assert body["total"] == 1
    assert body["items"][0]["filename"] == "synthetic-export-1.csv"


def test_list_assets_filters_by_type_and_environment(
    client: TestClient, db_session: Session
) -> None:
    workspace = Workspace(name="default")
    db_session.add(workspace)
    db_session.flush()

    db_session.add_all(
        [
            Asset(
                workspace_id=workspace.id,
                canonical_key="host:prod-01.example.test",
                name="prod-01.example.test",
                asset_type=AssetType.HOST,
                environment=AssetEnvironment.PRODUCTION,
            ),
            Asset(
                workspace_id=workspace.id,
                canonical_key="repo:example/synthetic-repo",
                name="example/synthetic-repo",
                asset_type=AssetType.REPOSITORY,
                environment=AssetEnvironment.DEVELOPMENT,
            ),
        ]
    )
    db_session.commit()

    response = client.get(
        "/api/v1/assets",
        params={"workspace_id": workspace.id, "asset_type": "host"},
    )
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["canonical_key"] == "host:prod-01.example.test"

    response = client.get(
        "/api/v1/assets",
        params={"workspace_id": workspace.id, "environment": "production"},
    )
    assert response.json()["total"] == 1


def test_list_endpoints_require_workspace_id(client: TestClient) -> None:
    assert client.get("/api/v1/sources").status_code == 422
    assert client.get("/api/v1/imports").status_code == 422
    assert client.get("/api/v1/assets").status_code == 422
    assert client.get("/api/v1/exposures").status_code == 422
