"""Login, sessions, lockout, and rate limiting (Milestone 7).

See docs/security.md for the full design this exercises.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import DEFAULT_TEST_PASSWORD, TestUser
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.audit_log import AuditLogEntry
from threnda.app.models.enums import AuditEventType
from threnda.app.models.user import User
from threnda.app.models.user_session import UserSession
from threnda.app.services.auth import LOGIN_LOCKOUT_THRESHOLD
from threnda.app.services.rate_limit import login_ip_rate_limiter
from threnda.app.web.cookies import SESSION_COOKIE_NAME


def test_login_succeeds_and_sets_session_cookie(app: FastAPI, admin_user: TestUser) -> None:
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/v1/auth/login",
            json={"username": admin_user.username, "password": admin_user.password},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"username": admin_user.username}
        assert SESSION_COOKIE_NAME in response.cookies


def test_login_normalizes_username_case_and_whitespace(app: FastAPI, admin_user: TestUser) -> None:
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/v1/auth/login",
            json={
                "username": f"  {admin_user.username.upper()}  ",
                "password": admin_user.password,
            },
        )
        assert response.status_code == 200, response.text


def test_login_wrong_password_and_unknown_username_look_identical(
    app: FastAPI, admin_user: TestUser
) -> None:
    with TestClient(app) as test_client:
        wrong_password = test_client.post(
            "/api/v1/auth/login",
            json={"username": admin_user.username, "password": "definitely-wrong-pw"},
        )
        unknown_user = test_client.post(
            "/api/v1/auth/login",
            json={"username": "no-such-user", "password": "definitely-wrong-pw"},
        )

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()
    assert SESSION_COOKIE_NAME not in wrong_password.cookies
    assert SESSION_COOKIE_NAME not in unknown_user.cookies


def test_repeated_failures_lock_the_account(
    app: FastAPI, admin_user: TestUser, db_session: Session
) -> None:
    with TestClient(app) as test_client:
        for _ in range(LOGIN_LOCKOUT_THRESHOLD):
            response = test_client.post(
                "/api/v1/auth/login",
                json={"username": admin_user.username, "password": "wrong"},
            )
            assert response.status_code == 401

        # Even the *correct* password is now rejected while locked.
        locked_response = test_client.post(
            "/api/v1/auth/login",
            json={"username": admin_user.username, "password": admin_user.password},
        )
        assert locked_response.status_code == 401
        assert locked_response.json()["detail"] == "Invalid username or password."

    user = db_session.get(User, admin_user.id)
    assert user is not None
    assert user.locked_until is not None
    assert user.locked_until > datetime.now(UTC)


def test_login_rate_limit_blocks_further_attempts_from_same_ip(
    app: FastAPI, admin_user: TestUser
) -> None:
    login_ip_rate_limiter.reset("testclient")
    try:
        with TestClient(app) as test_client:
            for _ in range(20):
                test_client.post(
                    "/api/v1/auth/login",
                    json={"username": "irrelevant", "password": "wrong"},
                )
            blocked = test_client.post(
                "/api/v1/auth/login",
                json={"username": admin_user.username, "password": admin_user.password},
            )
            assert blocked.status_code == 429
    finally:
        login_ip_rate_limiter.reset("testclient")


def test_logout_revokes_the_session(client: TestClient, db_session: Session) -> None:
    from webhelpers import extract_csrf

    session_cookie = client.cookies.get(SESSION_COOKIE_NAME)
    assert session_cookie is not None

    # Any page renders the same session-bound CSRF token (derived from the
    # session cookie + server secret, not stored) — /workspaces/new needs
    # no workspace membership, so it works regardless of what client has.
    new_workspace_page = client.get("/workspaces/new")
    token = extract_csrf(new_workspace_page.text)

    logout_response = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert logout_response.status_code == 303

    sessions = db_session.execute(select(UserSession)).scalars().all()
    assert any(s.revoked_at is not None for s in sessions)

    after_logout = client.get("/api/v1/auth/me")
    assert after_logout.status_code == 401


def test_me_requires_authentication(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_returns_current_username(client: TestClient, admin_user: TestUser) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json() == {"username": admin_user.username}


def test_expired_session_is_rejected(
    app: FastAPI, admin_user: TestUser, db_session: Session
) -> None:
    with TestClient(app) as test_client:
        test_client.post(
            "/api/v1/auth/login",
            json={"username": admin_user.username, "password": admin_user.password},
        )
        session_row = db_session.execute(select(UserSession)).scalars().one()
        session_row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db_session.commit()

        response = test_client.get("/api/v1/auth/me")
        assert response.status_code == 401


def test_login_success_and_failure_are_audited(
    app: FastAPI, admin_user: TestUser, db_session: Session
) -> None:
    with TestClient(app) as test_client:
        test_client.post(
            "/api/v1/auth/login",
            json={"username": admin_user.username, "password": "wrong"},
        )
        test_client.post(
            "/api/v1/auth/login",
            json={"username": admin_user.username, "password": admin_user.password},
        )

    events = db_session.execute(select(AuditLogEntry)).scalars().all()
    event_types = {e.event_type for e in events}
    assert AuditEventType.LOGIN_FAILED in event_types
    assert AuditEventType.LOGIN_SUCCEEDED in event_types
    for entry in events:
        assert entry.detail is None or DEFAULT_TEST_PASSWORD not in str(entry.detail)
