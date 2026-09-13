"""CSRF protection for the server-rendered web UI.

Every state-changing route requires an authenticated session (Milestone
7). The CSRF token is a synchronizer token cryptographically bound to that
session, not a bare double-submit cookie: it is
``HMAC-SHA256(THRENDA_SECRET_KEY, "csrf:" + session_token)``, recomputed on
every request from the session cookie the browser already sends and the
server's own secret. It needs no cookie of its own and no server-side
storage — an attacker who cannot read the (HttpOnly) session cookie's
value, and does not know the server's secret key, cannot compute it, and
it automatically rotates whenever the session does (e.g. on login — see
``threnda.app.services.auth.create_session``).

Before a session exists (the login page itself), there is nothing to bind
a token to, so a plain anonymous double-submit cookie is used instead —
this still defeats a cross-site login-CSRF attempt, since a foreign origin
cannot read the cookie's value to copy it into a forged request.

Usage in a route that renders a form::

    token = get_csrf_token(request)
    response = templates.TemplateResponse(request, "...", {"csrf_token": token, ...})
    apply_csrf_cookie(request, response, token)
    return response

Split into "compute the token" and "persist it on a response" because a
``Jinja2Templates`` response is fully rendered at construction time — the
token must be in the context *before* the response object exists, but the
cookie (only needed for the anonymous, pre-session case) can only be set
*on* that response object once it does.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request, Response

from threnda.app.web.cookies import SESSION_COOKIE_NAME, cookie_secure

ANON_COOKIE_NAME = "threnda_csrf_anon"
_TOKEN_BYTES = 32


def _session_bound_token(secret_key: str, session_token: str) -> str:
    return hmac.new(
        secret_key.encode(), f"csrf:{session_token}".encode(), hashlib.sha256
    ).hexdigest()


def get_csrf_token(request: Request) -> str:
    """The token to render into this page's forms.

    Derived from the session cookie when one is present; otherwise reuses
    the existing anonymous cookie's value, or generates a fresh one for
    ``apply_csrf_cookie`` to persist.
    """
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        secret_key: str = request.app.state.settings.secret_key
        return _session_bound_token(secret_key, session_token)
    return request.cookies.get(ANON_COOKIE_NAME) or secrets.token_urlsafe(_TOKEN_BYTES)


def apply_csrf_cookie(request: Request, response: Response, token: str) -> None:
    """Persist the anonymous CSRF cookie on ``response`` — a no-op once
    a session exists, since a session-bound token needs no cookie of its
    own (see the module docstring).
    """
    if request.cookies.get(SESSION_COOKIE_NAME):
        return
    if request.cookies.get(ANON_COOKIE_NAME) != token:
        response.set_cookie(
            ANON_COOKIE_NAME,
            token,
            httponly=True,
            samesite="strict",
            secure=cookie_secure(request.app.state.settings, request),
        )


def verify_csrf(request: Request, submitted: str | None) -> None:
    """Raise ``403`` unless ``submitted`` matches this request's expected CSRF token."""
    expected = get_csrf_token(request)
    if not submitted or not secrets.compare_digest(expected, submitted):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token.")
