"""Workspace onboarding and switching.

Any authenticated user may create a new workspace — doing so makes them
its first admin member (see ``threnda.app.services.memberships``).
Switching between workspaces a user already belongs to is handled by
``threnda.app.web.deps.get_current_membership``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from threnda.app.api.deps import get_db
from threnda.app.models.enums import WorkspaceRole
from threnda.app.models.user import User
from threnda.app.models.workspace import Workspace
from threnda.app.services.memberships import add_member
from threnda.app.web.cookies import WORKSPACE_COOKIE_NAME, cookie_secure
from threnda.app.web.csrf import apply_csrf_cookie, get_csrf_token, verify_csrf
from threnda.app.web.deps import get_current_user, templates

router = APIRouter(prefix="/workspaces", tags=["web-workspaces"])


@router.get("/new")
def new_workspace_form(
    request: Request, error: str | None = None, _user: User = Depends(get_current_user)
) -> Response:
    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request, "workspaces/new.html", {"error": error, "csrf_token": token}
    )
    apply_csrf_cookie(request, response, token)
    return response


@router.post("/new")
def create_workspace(
    request: Request,
    name: str = Form(...),
    csrf_token: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    if not user.is_org_admin:
        raise HTTPException(status_code=403, detail="Only a consultancy admin can create projects.")
    workspace = Workspace(name=name.strip())
    db.add(workspace)
    try:
        db.flush()
    except IntegrityError:
        # Roll back explicitly: this handler renders a normal response
        # instead of letting the exception propagate, so `iter_session`'s
        # own commit-on-success path would otherwise run against a
        # transaction the failed flush already left in a rollback-only
        # state.
        db.rollback()
        token = get_csrf_token(request)
        response = templates.TemplateResponse(
            request,
            "workspaces/new.html",
            {"error": f"A workspace named {name!r} already exists.", "csrf_token": token},
            status_code=409,
        )
        apply_csrf_cookie(request, response, token)
        return response

    add_member(db, workspace, user, WorkspaceRole.ADMIN, actor=user)

    redirect = RedirectResponse(url="/", status_code=303)
    redirect.set_cookie(
        WORKSPACE_COOKIE_NAME,
        str(workspace.id),
        httponly=True,
        samesite="strict",
        secure=cookie_secure(request.app.state.settings, request),
    )
    return redirect
