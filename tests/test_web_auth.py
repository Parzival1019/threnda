"""Web login/logout: forms, CSRF, cookies, lockout, rate limiting."""

from __future__ import annotations

from conftest import DEFAULT_TEST_PASSWORD, TestUser
from fastapi import FastAPI
from fastapi.testclient import TestClient
from webhelpers import bootstrap_workspace, extract_csrf

from threnda.app.services import auth as auth_service
from threnda.app.services.rate_limit import login_ip_rate_limiter


def test_login_page_renders_with_a_csrf_token(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/login")
    assert response.status_code == 200
    assert extract_csrf(response.text)


def test_successful_login_sets_a_session_cookie_and_redirects_home(
    anonymous_client: TestClient, admin_user: TestUser
) -> None:
    response = anonymous_client.get("/login")
    token = extract_csrf(response.text)
    response = anonymous_client.post(
        "/login",
        data={
            "csrf_token": token,
            "username": admin_user.username,
            "password": admin_user.password,
            "next": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.cookies.get("threnda_session")
    assert cookie is not None


def test_wrong_password_renders_a_generic_error(
    anonymous_client: TestClient, admin_user: TestUser
) -> None:
    response = anonymous_client.get("/login")
    token = extract_csrf(response.text)
    response = anonymous_client.post(
        "/login",
        data={
            "csrf_token": token,
            "username": admin_user.username,
            "password": "definitely-wrong",
            "next": "",
        },
    )
    assert response.status_code == 401
    assert "Invalid username or password." in response.text
    # Never leaks that the username itself was correct.
    assert admin_user.username not in response.text.split("Invalid username or password.")[0][-50:]


def test_unknown_username_renders_the_identical_generic_error(
    anonymous_client: TestClient,
) -> None:
    response = anonymous_client.get("/login")
    token = extract_csrf(response.text)
    response = anonymous_client.post(
        "/login",
        data={
            "csrf_token": token,
            "username": "nobody-at-all",
            "password": "whatever123",
            "next": "",
        },
    )
    assert response.status_code == 401
    assert "Invalid username or password." in response.text


def test_login_requires_a_valid_csrf_token(
    anonymous_client: TestClient, admin_user: TestUser
) -> None:
    response = anonymous_client.post(
        "/login",
        data={
            "csrf_token": "not-the-real-token",
            "username": admin_user.username,
            "password": admin_user.password,
            "next": "",
        },
    )
    assert response.status_code == 403


def test_open_redirect_via_next_is_rejected(
    anonymous_client: TestClient, admin_user: TestUser
) -> None:
    for unsafe_next in ("https://evil.example/", "//evil.example/", "http://evil.example"):
        anonymous_client.cookies.clear()
        response = anonymous_client.get("/login")
        token = extract_csrf(response.text)
        response = anonymous_client.post(
            "/login",
            data={
                "csrf_token": token,
                "username": admin_user.username,
                "password": admin_user.password,
                "next": unsafe_next,
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/"


def test_safe_next_path_is_honored(anonymous_client: TestClient, admin_user: TestUser) -> None:
    response = anonymous_client.get("/login")
    token = extract_csrf(response.text)
    response = anonymous_client.post(
        "/login",
        data={
            "csrf_token": token,
            "username": admin_user.username,
            "password": admin_user.password,
            "next": "/sources",
        },
        follow_redirects=False,
    )
    assert response.headers["location"] == "/sources"


def test_repeated_failed_logins_lock_the_account(
    anonymous_client: TestClient, admin_user: TestUser
) -> None:
    for _ in range(auth_service.LOGIN_LOCKOUT_THRESHOLD):
        response = anonymous_client.get("/login")
        token = extract_csrf(response.text)
        anonymous_client.post(
            "/login",
            data={
                "csrf_token": token,
                "username": admin_user.username,
                "password": "wrong",
                "next": "",
            },
        )
    # Even the *correct* password now renders the same generic failure.
    response = anonymous_client.get("/login")
    token = extract_csrf(response.text)
    response = anonymous_client.post(
        "/login",
        data={
            "csrf_token": token,
            "username": admin_user.username,
            "password": admin_user.password,
            "next": "",
        },
    )
    assert response.status_code == 401
    assert "Invalid username or password." in response.text


def test_per_ip_rate_limit_blocks_further_attempts(
    anonymous_client: TestClient, admin_user: TestUser
) -> None:
    try:
        for _ in range(21):
            response = anonymous_client.get("/login")
            token = extract_csrf(response.text)
            last = anonymous_client.post(
                "/login",
                data={
                    "csrf_token": token,
                    "username": f"nonexistent-{_}",
                    "password": "whatever123",
                    "next": "",
                },
            )
        assert last.status_code == 429
    finally:
        login_ip_rate_limiter.clear()


def test_logout_revokes_the_session_and_clears_the_cookie(client: TestClient) -> None:
    # `client` is already logged in (see tests/conftest.py) but has no
    # workspace yet — create one so a protected page actually renders.
    workspace_id = bootstrap_workspace(client)

    response = client.get("/exposures", params={"workspace_id": workspace_id})
    assert response.status_code == 200
    token = extract_csrf(response.text)
    logout_response = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/login"

    # The old cookie no longer works.
    after = client.get("/exposures", params={"workspace_id": workspace_id}, follow_redirects=False)
    assert after.status_code == 302
    assert after.headers["location"].startswith("/login")


def test_anonymous_request_to_a_protected_page_redirects_to_login(
    anonymous_client: TestClient,
) -> None:
    response = anonymous_client.get("/exposures", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("/login")


def test_session_cookie_flags(app: FastAPI, admin_user: TestUser) -> None:
    with TestClient(app) as anon:
        response = anon.get("/login")
        token = extract_csrf(response.text)
        response = anon.post(
            "/login",
            data={
                "csrf_token": token,
                "username": admin_user.username,
                "password": admin_user.password,
                "next": "",
            },
            follow_redirects=False,
        )
        set_cookie = response.headers.get("set-cookie", "")
        assert "httponly" in set_cookie.lower()
        assert "samesite=strict" in set_cookie.lower()
        # Development settings (see the `settings` fixture) use plain HTTP,
        # so Secure should not be set here — this is the negative case for
        # threnda.app.web.cookies.cookie_secure; the positive case (Secure
        # set under HTTPS/production) is covered in test_web_cookies.py.
        assert "secure" not in set_cookie.lower()


def test_bootstrap_password_meets_the_policy() -> None:
    # A sanity check on the shared test fixture constant itself, so a
    # future accidental weakening of DEFAULT_TEST_PASSWORD fails loudly
    # here instead of producing confusing failures elsewhere.
    auth_service.validate_password_strength(DEFAULT_TEST_PASSWORD, username="admin")
