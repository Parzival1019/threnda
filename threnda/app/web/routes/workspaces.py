"""Workspace onboarding and switching.

No authentication exists yet (Milestone 7): a "workspace" here is just the
first one to exist, or whichever the visitor picked — see
``threnda.app.web.deps.get_current_workspace``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from threnda.app.api.deps import get_db
from threnda.app.models.workspace import Workspace
from threnda.app.web.csrf import apply_csrf_cookie, get_csrf_token, verify_csrf
from threnda.app.web.deps import WORKSPACE_COOKIE_NAME, templates

router = APIRouter(prefix="/workspaces", tags=["web-workspaces"])


@router.get("/new")
def new_workspace_form(request: Request, error: str | None = None) -> Response:
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
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
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

    redirect = RedirectResponse(url="/", status_code=303)
    redirect.set_cookie(WORKSPACE_COOKIE_NAME, str(workspace.id), samesite="lax")
    return redirect
