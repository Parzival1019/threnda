"""Tests for workspace onboarding and the no-workspace redirect."""

from __future__ import annotations

from fastapi.testclient import TestClient
from webhelpers import bootstrap_workspace, extract_csrf


def test_root_redirects_to_onboarding_when_no_workspace_exists(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/workspaces/new"


def test_creating_a_workspace_sets_cookie_and_redirects_home(client: TestClient) -> None:
    response = client.get("/workspaces/new")
    assert response.status_code == 200
    token = extract_csrf(response.text)

    response = client.post(
        "/workspaces/new",
        data={"name": "synthetic-workspace", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert response.cookies.get("threnda_workspace_id") is not None


def test_duplicate_workspace_name_shows_an_error_not_a_500(client: TestClient) -> None:
    bootstrap_workspace(client, name="dup-name")

    response = client.get("/workspaces/new")
    token = extract_csrf(response.text)
    response = client.post(
        "/workspaces/new", data={"name": "dup-name", "csrf_token": token}, follow_redirects=False
    )
    assert response.status_code == 409
    assert "already exists" in response.text


def test_missing_csrf_token_is_rejected(client: TestClient) -> None:
    response = client.post("/workspaces/new", data={"name": "no-csrf"})
    assert response.status_code == 422


def test_wrong_csrf_token_is_rejected(client: TestClient) -> None:
    client.get("/workspaces/new")
    response = client.post(
        "/workspaces/new", data={"name": "wrong-csrf", "csrf_token": "not-the-real-token"}
    )
    assert response.status_code == 403
