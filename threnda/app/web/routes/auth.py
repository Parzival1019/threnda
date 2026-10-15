"""Login and logout for the server-rendered web UI.

Every failure (unknown username, wrong password, locked account, rate
limited) renders the exact same generic message — see
``threnda.app.services.auth`` for why distinguishing them to the client
would turn the login form into a username oracle. The real reason is only
ever in the audit log and, for a lockout, the account row itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from threnda.app.api.deps import get_db
from threnda.app.models.enums import AuditEventType
from threnda.app.models.user import User
from threnda.app.services.audit import record_audit_event
from threnda.app.services.auth import (
    AccountLockedError,
    InvalidCredentialsError,
    authenticate,
    create_session,
    revoke_session,
)
from threnda.app.services.rate_limit import login_ip_rate_limiter
from threnda.app.web.cookies import SESSION_COOKIE_NAME, cookie_secure
from threnda.app.web.csrf import apply_csrf_cookie, get_csrf_token, verify_csrf
from threnda.app.web.deps import get_current_user, templates

router = APIRouter(tags=["web-auth"])

GENERIC_LOGIN_ERROR = "Invalid username or password."
LOGIN_SESSION_MAX_AGE = 60 * 60 * 12  # matches SESSION_ABSOLUTE_TIMEOUT


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _safe_next(next_path: str | None) -> str:
    """Only ever redirect within this app — never an open redirect.

    ``next`` comes from a query parameter an attacker fully controls (a
    crafted login link); accepting an absolute or protocol-relative URL
    there would let a phishing link disguise itself as "log in to
    Threnda, redirected safely" while actually forwarding the
    authenticated browser session's follow-on click elsewhere.
    """
    if next_path and next_path.startswith("/") and not next_path.startswith("//"):
        return next_path
    return "/"


_NOTICES = {"account-created": "Your account is ready — log in with your new password."}


@router.get("/login")
def login_form(
    request: Request,
    next: str | None = None,
    error: str | None = None,
    notice: str | None = None,
) -> Response:
    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request,
        "login.html",
        {
            "csrf_token": token,
            "next": next or "",
            "error": error,
            # Only fixed, known messages — never echo the query string.
            "notice": _NOTICES.get(notice or ""),
            "demo_mode": request.app.state.settings.demo_mode,
        },
    )
    apply_csrf_cookie(request, response, token)
    return response


@router.post("/login")
def login_submit(
    request: Request,
    csrf_token: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default=""),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    ip = _client_ip(request)

    if ip is not None and login_ip_rate_limiter.is_limited(ip):
        token = get_csrf_token(request)
        response = templates.TemplateResponse(
            request,
            "login.html",
            {
                "csrf_token": token,
                "next": next,
                "error": "Too many login attempts. Try again in a few minutes.",
            },
            status_code=429,
        )
        apply_csrf_cookie(request, response, token)
        return response

    try:
        user = authenticate(db, username, password)
    except (InvalidCredentialsError, AccountLockedError) as exc:
        if ip is not None:
            login_ip_rate_limiter.hit(ip)
        record_audit_event(
            db,
            event_type=AuditEventType.LOGIN_FAILED,
            actor=None,
            detail={"username": username.strip().lower()[:64], "reason": type(exc).__name__},
            ip_address=ip,
        )
        token = get_csrf_token(request)
        response = templates.TemplateResponse(
            request,
            "login.html",
            {"csrf_token": token, "next": next, "error": GENERIC_LOGIN_ERROR},
            status_code=401,
        )
        apply_csrf_cookie(request, response, token)
        return response

    issued = create_session(db, user, user_agent=request.headers.get("user-agent"), ip_address=ip)
    record_audit_event(db, event_type=AuditEventType.LOGIN_SUCCEEDED, actor=user, ip_address=ip)

    redirect = RedirectResponse(url=_safe_next(next), status_code=303)
    redirect.set_cookie(
        SESSION_COOKIE_NAME,
        issued.token,
        httponly=True,
        samesite="strict",
        secure=cookie_secure(request.app.state.settings, request),
        max_age=LOGIN_SESSION_MAX_AGE,
    )
    return redirect


@router.post("/logout")
def logout(
    request: Request,
    csrf_token: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    session = request.state.session
    revoke_session(db, session)
    record_audit_event(
        db, event_type=AuditEventType.LOGOUT, actor=user, ip_address=_client_ip(request)
    )
    redirect = RedirectResponse(url="/login", status_code=303)
    redirect.delete_cookie(SESSION_COOKIE_NAME)
    return redirect
