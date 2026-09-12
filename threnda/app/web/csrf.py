"""CSRF protection for the server-rendered web UI.

There is no session/authentication yet (Milestone 7), so there is nothing
to tie a CSRF token to except the request itself — a signed session-bound
token is not available. This uses the standard double-submit-cookie
pattern instead: a random token is set as an httponly cookie the first
time a page is rendered, echoed into every form as a hidden field, and
compared back to the cookie on every state-changing request. This proves
the request originated from a page this server rendered (a cross-site
form cannot read the cookie to copy its value), which is exactly what
CSRF protection needs here — it is not an authentication mechanism and
makes no claim about *who* is making the request, same caveat as the
`actor` placeholder used throughout the API.

Usage in a route that renders a form::

    token = get_csrf_token(request)
    response = templates.TemplateResponse(request, "...", {"csrf_token": token, ...})
    apply_csrf_cookie(request, response, token)
    return response

Split into "compute the token" and "persist it on a response" because a
``Jinja2Templates`` response is fully rendered at construction time — the
token must be in the context *before* the response object exists, but the
cookie can only be set *on* that response object once it does.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, Response

COOKIE_NAME = "threnda_csrf"
_TOKEN_BYTES = 32


def get_csrf_token(request: Request) -> str:
    """The token to render into this page's forms.

    Reuses the existing cookie's value if present, so repeated page loads
    in the same browser session keep the same token; otherwise generates a
    fresh one for ``apply_csrf_cookie`` to persist.
    """
    return request.cookies.get(COOKIE_NAME) or secrets.token_urlsafe(_TOKEN_BYTES)


def apply_csrf_cookie(request: Request, response: Response, token: str) -> None:
    """Persist ``token`` as the CSRF cookie on ``response`` if it isn't already set."""
    if request.cookies.get(COOKIE_NAME) != token:
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )


def verify_csrf(request: Request, submitted: str | None) -> None:
    """Raise ``403`` unless ``submitted`` matches the request's CSRF cookie."""
    cookie_token = request.cookies.get(COOKIE_NAME)
    if not cookie_token or not submitted or not secrets.compare_digest(cookie_token, submitted):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token.")
