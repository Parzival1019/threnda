"""Shared FastAPI dependencies for the web UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import Depends, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app import __version__
from threnda.app.api.deps import get_db
from threnda.app.models.candidate_finding import CandidateFinding
from threnda.app.models.document import Document
from threnda.app.models.enums import CandidateFindingStatus, ExposureStatus, PossibleDuplicateStatus
from threnda.app.models.exposure import Exposure
from threnda.app.models.import_run import ImportRun
from threnda.app.models.possible_duplicate import PossibleDuplicate
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace
from threnda.app.web.errors import NoWorkspaceError
from threnda.app.web.filters import register_filters


def merge_query(request: Request, **overrides: str | int | None) -> str:
    """The current request's query string with ``overrides`` applied.

    ``None`` removes a key. Used by pagination/sort/filter links so each
    one only changes the parameter it cares about, keeping every other
    active filter intact — both as a plain ``href`` (works with no JS at
    all) and as the target of an htmx-enhanced request.
    """
    params = dict(request.query_params)
    for key, value in overrides.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = str(value)
    return urlencode(params)


TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
register_filters(templates.env)
templates.env.globals["merge_query"] = merge_query

WORKSPACE_COOKIE_NAME = "threnda_workspace_id"


def get_current_workspace(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> Workspace:
    """Resolve which workspace this request is scoped to.

    No authentication exists yet (Milestone 7), so there is no session to
    derive this from. Precedence: an explicit ``?workspace_id=`` query
    param (validated) > the ``threnda_workspace_id`` cookie (validated,
    refreshed to match a query-param override) > the first workspace by
    id. Raises ``NoWorkspaceError`` (redirected to onboarding) if none
    exist at all. The cookie is a plain browsing convenience, not an
    access boundary — identical caveat to the JSON API's caller-supplied
    ``workspace_id`` (see ``api/v1/exposures.py``).
    """
    query_workspace_id = request.query_params.get("workspace_id")
    if query_workspace_id is not None:
        try:
            workspace_id = int(query_workspace_id)
        except ValueError:
            workspace_id = -1
        workspace = db.get(Workspace, workspace_id)
        if workspace is not None:
            response.set_cookie(WORKSPACE_COOKIE_NAME, str(workspace.id), samesite="lax")
            return workspace

    cookie_workspace_id = request.cookies.get(WORKSPACE_COOKIE_NAME)
    if cookie_workspace_id is not None:
        try:
            workspace = db.get(Workspace, int(cookie_workspace_id))
        except ValueError:
            workspace = None
        if workspace is not None:
            return workspace

    first_workspace = db.execute(
        select(Workspace).order_by(Workspace.id).limit(1)
    ).scalar_one_or_none()
    if first_workspace is None:
        raise NoWorkspaceError
    return first_workspace


def build_shell_context(db: Session, workspace: Workspace) -> dict[str, Any]:
    """Sidebar/topbar data every full-page template needs (nav counts, footer)."""
    open_count = db.execute(
        select(func.count())
        .select_from(Exposure)
        .where(Exposure.workspace_id == workspace.id, Exposure.status == ExposureStatus.OPEN)
    ).scalar_one()
    review_count = db.execute(
        select(func.count())
        .select_from(PossibleDuplicate)
        .where(
            PossibleDuplicate.workspace_id == workspace.id,
            PossibleDuplicate.status == PossibleDuplicateStatus.PENDING,
        )
    ).scalar_one()
    source_count = db.execute(
        select(func.count()).select_from(Source).where(Source.workspace_id == workspace.id)
    ).scalar_one()
    import_count = db.execute(
        select(func.count()).select_from(ImportRun).where(ImportRun.workspace_id == workspace.id)
    ).scalar_one()
    last_import = db.execute(
        select(ImportRun.started_at)
        .where(ImportRun.workspace_id == workspace.id)
        .order_by(ImportRun.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    document_count = db.execute(
        select(func.count()).select_from(Document).where(Document.workspace_id == workspace.id)
    ).scalar_one()
    candidate_count = db.execute(
        select(func.count())
        .select_from(CandidateFinding)
        .where(
            CandidateFinding.workspace_id == workspace.id,
            CandidateFinding.status == CandidateFindingStatus.PENDING,
        )
    ).scalar_one()

    return {
        "workspace": workspace,
        "nav_open_count": open_count,
        "nav_review_count": review_count,
        "nav_source_count": source_count,
        "nav_import_count": import_count,
        "nav_document_count": document_count,
        "nav_candidate_count": candidate_count,
        "footer_source_count": source_count,
        "footer_last_import": last_import,
        "app_version": __version__,
    }
