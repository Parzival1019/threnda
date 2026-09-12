"""Tests for the sources list/create page."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from webhelpers import bootstrap_workspace, extract_csrf

from threnda.app.models.enums import SourceType
from threnda.app.models.source import Source


def test_sources_list_shows_empty_state(client: TestClient) -> None:
    bootstrap_workspace(client)
    response = client.get("/sources")
    assert response.status_code == 200
    assert "No sources yet" in response.text


def test_creating_a_source_persists_it_and_lists_it(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    response = client.get("/sources")
    token = extract_csrf(response.text)

    response = client.post(
        "/sources",
        data={"csrf_token": token, "name": "nightly-sca", "source_type": "sarif"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    sources = db_session.scalars(select(Source).where(Source.workspace_id == workspace_id)).all()
    assert len(sources) == 1
    assert sources[0].name == "nightly-sca"
    assert sources[0].source_type == SourceType.SARIF

    listing = client.get("/sources")
    assert "nightly-sca" in listing.text


def test_creating_a_source_without_csrf_is_rejected(client: TestClient) -> None:
    bootstrap_workspace(client)
    response = client.post("/sources", data={"name": "x", "source_type": "json"})
    assert response.status_code == 422
