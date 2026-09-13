"""JSON API session login/logout.

Uses the same cookie-based session as the web UI (``threnda_session``,
``SameSite=Strict``) rather than a bearer token — a single session
mechanism for both surfaces, and ``SameSite=Strict`` on its own already
stops a cross-site request from carrying the cookie at all, which is why
these JSON endpoints (unlike the HTML form endpoints in
``threnda.app.web.routes.auth``) do not additionally require a CSRF token:
there is no browser form submission vector here for one to defend against.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from threnda.app.api.deps import get_current_user, get_db
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

router = APIRouter(prefix="/auth", tags=["auth"])

GENERIC_LOGIN_ERROR = "Invalid username or password."
LOGIN_SESSION_MAX_AGE = 60 * 60 * 12


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=256)


class MeResponse(BaseModel):
    username: str


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/login")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    ip = _client_ip(request)
    if ip is not None and login_ip_rate_limiter.is_limited(ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")

    try:
        user = authenticate(db, payload.username, payload.password)
    except (InvalidCredentialsError, AccountLockedError):
        if ip is not None:
            login_ip_rate_limiter.hit(ip)
        record_audit_event(
            db,
            event_type=AuditEventType.LOGIN_FAILED,
            actor=None,
            detail={"username": payload.username.strip().lower()[:64]},
            ip_address=ip,
        )
        # A JSONResponse, not `raise HTTPException`: raising would propagate
        # through the request-scoped session's generator dependency
        # (threnda.app.db.session.iter_session), which rolls back on *any*
        # exception — silently discarding the failed-login counter
        # increment and the audit entry just written above. Returning a
        # response directly lets the request complete normally, so those
        # writes commit exactly like a successful login's do. Same pattern
        # as the import upload endpoint's failed-parse handling — see
        # docs/importers.md, "Transaction semantics".
        return JSONResponse(status_code=401, content={"detail": GENERIC_LOGIN_ERROR})

    issued = create_session(db, user, user_agent=request.headers.get("user-agent"), ip_address=ip)
    record_audit_event(db, event_type=AuditEventType.LOGIN_SUCCEEDED, actor=user, ip_address=ip)

    body = jsonable_encoder(MeResponse(username=user.username))
    json_response = JSONResponse(content=body)
    json_response.set_cookie(
        SESSION_COOKIE_NAME,
        issued.token,
        httponly=True,
        samesite="strict",
        secure=cookie_secure(request.app.state.settings, request),
        max_age=LOGIN_SESSION_MAX_AGE,
    )
    return json_response


@router.post("/logout", status_code=204)
def logout(
    request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> None:
    revoke_session(db, request.state.session)
    record_audit_event(
        db, event_type=AuditEventType.LOGOUT, actor=user, ip_address=_client_ip(request)
    )


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user)) -> MeResponse:
    return MeResponse(username=user.username)
