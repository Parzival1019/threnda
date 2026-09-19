"""Loopback-only first-run onboarding for the packaged Community Desktop app."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from threnda.app.api.deps import get_db
from threnda.app.models.enums import AuditEventType
from threnda.app.services.audit import record_audit_event
from threnda.app.services.auth import InvalidUsernameError, WeakPasswordError, create_session
from threnda.app.services.bootstrap import (
    InstanceAlreadyInitializedError,
    create_first_admin,
    has_accounts,
)
from threnda.app.services.user_accounts import UsernameTakenError
from threnda.app.web.cookies import SESSION_COOKIE_NAME, cookie_secure
from threnda.app.web.csrf import apply_csrf_cookie, get_csrf_token, verify_csrf
from threnda.app.web.deps import templates

router = APIRouter(tags=["desktop"])

DESKTOP_SETUP_COOKIE = "threnda_desktop_setup"
DESKTOP_SETUP_MAX_AGE = 10 * 60
SESSION_MAX_AGE = 60 * 60 * 12
_LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "testclient"}


def _desktop_token(request: Request) -> str:
    token: str | None = getattr(request.app.state, "desktop_setup_token", None)
    client = request.client.host if request.client else None
    if not getattr(request.app.state, "desktop_mode", False) or client not in _LOOPBACK_CLIENTS:
        raise HTTPException(status_code=404)
    if not token:
        raise HTTPException(status_code=404)
    return token


def _valid_setup_cookie(request: Request, token: str) -> bool:
    supplied = request.cookies.get(DESKTOP_SETUP_COOKIE)
    return bool(supplied and secrets.compare_digest(supplied, token))


def _setup_page(request: Request, *, error: str | None = None, status_code: int = 200) -> Response:
    csrf_token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request,
        "desktop/setup.html",
        {"csrf_token": csrf_token, "error": error},
        status_code=status_code,
    )
    apply_csrf_cookie(request, response, csrf_token)
    return response


@router.get("/desktop/start")
def desktop_start(request: Request, token: str, db: Session = Depends(get_db)) -> Response:
    """Exchange the in-memory launch token for a short-lived HttpOnly cookie."""
    expected = _desktop_token(request)
    if has_accounts(db):
        return RedirectResponse(url="/login", status_code=303)
    if not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=404)
    response = RedirectResponse(url="/desktop/setup", status_code=303)
    response.set_cookie(
        DESKTOP_SETUP_COOKIE,
        expected,
        httponly=True,
        samesite="strict",
        secure=cookie_secure(request.app.state.settings, request),
        max_age=DESKTOP_SETUP_MAX_AGE,
        path="/desktop/setup",
    )
    return response


@router.get("/desktop/setup")
def desktop_setup_form(request: Request, db: Session = Depends(get_db)) -> Response:
    token = _desktop_token(request)
    if has_accounts(db):
        return RedirectResponse(url="/login", status_code=303)
    if not _valid_setup_cookie(request, token):
        raise HTTPException(status_code=404)
    return _setup_page(request)


@router.post("/desktop/setup")
def desktop_setup_submit(
    request: Request,
    csrf_token: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    token = _desktop_token(request)
    if not _valid_setup_cookie(request, token):
        raise HTTPException(status_code=404)
    verify_csrf(request, csrf_token)
    if password != password_confirm:
        return _setup_page(request, error="Passwords did not match.", status_code=400)

    try:
        user, _workspace = create_first_admin(db, username, password)
    except InstanceAlreadyInitializedError:
        return RedirectResponse(url="/login", status_code=303)
    except (InvalidUsernameError, WeakPasswordError, UsernameTakenError) as exc:
        return _setup_page(request, error=str(exc), status_code=400)

    issued = create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.LOGIN_SUCCEEDED,
        actor=user,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    request.app.state.desktop_setup_token = None
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        issued.token,
        httponly=True,
        samesite="strict",
        secure=cookie_secure(request.app.state.settings, request),
        max_age=SESSION_MAX_AGE,
    )
    response.delete_cookie(DESKTOP_SETUP_COOKIE, path="/desktop/setup")
    return response
