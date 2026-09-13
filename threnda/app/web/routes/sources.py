"""Sources: list and create."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse, Response

from threnda.app.api.deps import get_db
from threnda.app.models.enums import SourceType
from threnda.app.models.source import Source
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.web.csrf import apply_csrf_cookie, get_csrf_token, verify_csrf
from threnda.app.web.deps import (
    build_shell_context,
    get_current_membership,
    require_analyst,
    templates,
)

router = APIRouter(prefix="/sources", tags=["web-sources"])


@router.get("")
def list_sources(
    request: Request,
    error: str | None = None,
    membership: WorkspaceMembership = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> Response:
    workspace = membership.workspace
    sources = (
        db.execute(select(Source).where(Source.workspace_id == workspace.id).order_by(Source.name))
        .scalars()
        .all()
    )

    token = get_csrf_token(request)
    context = {
        "active_nav": "sources",
        "sources": sources,
        "source_types": list(SourceType),
        "error": error,
        "csrf_token": token,
        **build_shell_context(request, db, workspace, membership),
    }
    response = templates.TemplateResponse(request, "sources/list.html", context)
    apply_csrf_cookie(request, response, token)
    return response


@router.post("")
def create_source(
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(...),
    source_type: str = Form(...),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    workspace = membership.workspace
    try:
        resolved_type = SourceType(source_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown source type {source_type!r}") from exc
    source = Source(workspace_id=workspace.id, name=name.strip(), source_type=resolved_type)
    db.add(source)
    db.flush()
    return RedirectResponse(url=f"/sources?workspace_id={workspace.id}", status_code=303)
