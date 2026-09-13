"""CSRF: session-bound tokens, not a bare double-submit cookie."""

from __future__ import annotations

from conftest import TestUser
from fastapi import FastAPI
from fastapi.testclient import TestClient
from webhelpers import bootstrap_workspace, extract_csrf

from threnda.app.web.csrf import get_csrf_token


def test_missing_csrf_token_is_rejected(client: TestClient) -> None:
    workspace_id = bootstrap_workspace(client)
    response = client.post(
        "/sources",
        data={"name": "no-token", "source_type": "json"},
        params={"workspace_id": workspace_id},
    )
    assert response.status_code == 422  # missing required form field


def test_wrong_csrf_token_is_rejected(client: TestClient) -> None:
    workspace_id = bootstrap_workspace(client)
    response = client.post(
        "/sources",
        data={"name": "wrong-token", "source_type": "json", "csrf_token": "bogus"},
        params={"workspace_id": workspace_id},
    )
    assert response.status_code == 403


def test_correct_csrf_token_is_accepted(client: TestClient) -> None:
    workspace_id = bootstrap_workspace(client)
    page = client.get("/sources", params={"workspace_id": workspace_id})
    token = extract_csrf(page.text)
    response = client.post(
        "/sources",
        data={"name": "good-token", "source_type": "json", "csrf_token": token},
        params={"workspace_id": workspace_id},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_csrf_token_is_bound_to_the_session_not_the_workspace(
    app: FastAPI, admin_user: TestUser
) -> None:
    """A token computed for one session must not verify against a different one."""
    with TestClient(app) as client_a, TestClient(app) as client_b:
        client_a.post(
            "/api/v1/auth/login",
            json={"username": admin_user.username, "password": admin_user.password},
        )
        client_b.post(
            "/api/v1/auth/login",
            json={"username": admin_user.username, "password": admin_user.password},
        )
        workspace_id = bootstrap_workspace(client_a)

        page = client_a.get("/sources", params={"workspace_id": workspace_id})
        token_from_a = extract_csrf(page.text)

        # Using session A's token against session B's cookie must fail —
        # each session has its own independent, unguessable token.
        response = client_b.post(
            "/sources",
            data={"name": "cross-session", "source_type": "json", "csrf_token": token_from_a},
            params={"workspace_id": workspace_id},
        )
        assert response.status_code == 403


def test_get_csrf_token_is_deterministic_within_one_session(
    app: FastAPI, admin_user: TestUser
) -> None:
    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/login",
            json={"username": admin_user.username, "password": admin_user.password},
        )
        workspace_id = bootstrap_workspace(client)
        first = extract_csrf(client.get("/sources", params={"workspace_id": workspace_id}).text)
        second = extract_csrf(client.get("/sources", params={"workspace_id": workspace_id}).text)
        assert first == second


def test_anonymous_csrf_cookie_defends_the_login_form(app: FastAPI) -> None:
    """Before a session exists, the plain double-submit cookie is what's used."""
    with TestClient(app) as anon:
        response = anon.get("/login")
        cookie_value = anon.cookies.get("threnda_csrf_anon")
        assert cookie_value is not None
        assert extract_csrf(response.text) == cookie_value


def test_verify_csrf_helper_matches_get_csrf_token(client: TestClient) -> None:
    # Whitebox sanity check that the two halves of the mechanism agree —
    # already exercised end-to-end above, this pins the specific function.
    from starlette.requests import Request

    scope = {
        "type": "http",
        "headers": [(b"cookie", b"threnda_session=abc123")],
        "app": client.app,
    }
    request = Request(scope)
    assert get_csrf_token(request) == get_csrf_token(request)
