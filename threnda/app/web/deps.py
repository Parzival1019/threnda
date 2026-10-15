"""Shared FastAPI dependencies for the web UI."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app import __version__
from threnda.app.api.deps import get_db
from threnda.app.models.candidate_finding import CandidateFinding
from threnda.app.models.document import Document
from threnda.app.models.enums import (
    CandidateFindingStatus,
    ExposureStatus,
    PossibleDuplicateStatus,
    WorkspaceRole,
)
from threnda.app.models.exposure import Exposure
from threnda.app.models.import_run import ImportRun
from threnda.app.models.possible_duplicate import PossibleDuplicate
from threnda.app.models.source import Source
from threnda.app.models.user import User
from threnda.app.models.workspace import Workspace
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.services.auth import validate_session
from threnda.app.services.memberships import role_at_least
from threnda.app.web.cookies import SESSION_COOKIE_NAME, WORKSPACE_COOKIE_NAME
from threnda.app.web.csrf import get_csrf_token
from threnda.app.web.errors import ClientPortalOnlyError, NotAuthenticatedError, NoWorkspaceError
from threnda.app.web.filters import register_filters
from threnda.app.web.resources import web_resource_dir


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


TEMPLATES_DIR = web_resource_dir("templates")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
register_filters(templates.env)
templates.env.globals["merge_query"] = merge_query


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Resolve the authenticated user from the session cookie.

    Raises ``NotAuthenticatedError`` (redirected to ``/login?next=...`` by
    ``threnda.app.web.errors``) rather than a bare ``401`` — a browser
    session with no/an expired cookie is the ordinary "please log in"
    case, not an API error response.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    session = validate_session(db, token)
    if session is None:
        raise NotAuthenticatedError(next_path=request.url.path)
    request.state.session = session
    return session.user


def _lookup_membership(db: Session, user_id: int, workspace_id: int) -> WorkspaceMembership | None:
    """A *consultancy* (non-client) membership — the only kind the
    consultancy UI ever resolves. Client memberships are served exclusively
    by ``threnda.app.web.routes.portal``."""
    return db.execute(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.role != WorkspaceRole.CLIENT,
        )
    ).scalar_one_or_none()


def get_current_membership(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceMembership:
    """Resolve which workspace this request is scoped to, and the caller's role in it.

    Precedence: an explicit ``?workspace_id=`` query param > the
    ``threnda_workspace_id`` cookie > the user's first workspace by id.
    Unlike before Milestone 7, a ``workspace_id`` (from any source) only
    ever resolves to a workspace the authenticated user actually has a
    ``WorkspaceMembership`` in — one they don't belong to is silently
    skipped, falling through to the cookie/default, exactly like an id
    that names no workspace at all. Raises ``NoWorkspaceError``
    (redirected to onboarding) if the user has no workspace at all.
    """
    query_workspace_id = request.query_params.get("workspace_id")
    if query_workspace_id is not None:
        try:
            workspace_id = int(query_workspace_id)
        except ValueError:
            workspace_id = -1
        membership = _lookup_membership(db, user.id, workspace_id)
        if membership is not None:
            # Remember the explicitly chosen project. Setting the cookie on
            # the injected ``response`` alone is lost whenever the route
            # returns its own Response object (every template route does),
            # so ``remember_workspace_cookie`` in threnda.app.main applies it.
            request.state.remember_workspace_id = workspace_id
            return membership

    cookie_workspace_id = request.cookies.get(WORKSPACE_COOKIE_NAME)
    if cookie_workspace_id is not None:
        try:
            membership = _lookup_membership(db, user.id, int(cookie_workspace_id))
        except ValueError:
            membership = None
        if membership is not None:
            return membership

    first_membership = db.execute(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.user_id == user.id,
            WorkspaceMembership.role != WorkspaceRole.CLIENT,
        )
        .order_by(WorkspaceMembership.workspace_id)
        .limit(1)
    ).scalar_one_or_none()
    if first_membership is None:
        if user.client_id is not None or _has_client_membership(db, user.id):
            raise ClientPortalOnlyError
        raise NoWorkspaceError
    return first_membership


def _has_client_membership(db: Session, user_id: int) -> bool:
    return (
        db.execute(
            select(WorkspaceMembership.id)
            .where(
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.role == WorkspaceRole.CLIENT,
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def get_current_workspace(
    membership: WorkspaceMembership = Depends(get_current_membership),
) -> Workspace:
    return membership.workspace


def require_analyst(
    membership: WorkspaceMembership = Depends(get_current_membership),
) -> WorkspaceMembership:
    """Require at least the ``analyst`` role — the floor for any write action."""
    if not role_at_least(membership.role, WorkspaceRole.ANALYST):
        raise HTTPException(status_code=403, detail="Insufficient permissions for this action.")
    return membership


def require_admin(
    membership: WorkspaceMembership = Depends(get_current_membership),
) -> WorkspaceMembership:
    """Require the ``admin`` role — member/webhook management."""
    if not role_at_least(membership.role, WorkspaceRole.ADMIN):
        raise HTTPException(status_code=403, detail="Insufficient permissions for this action.")
    return membership


def build_shell_context(
    request: Request, db: Session, workspace: Workspace, membership: WorkspaceMembership
) -> dict[str, Any]:
    """Sidebar/topbar data every full-page template needs (nav counts, footer,
    and a CSRF token for the sidebar's logout form — every full-page route
    renders the shell, so it is generated once here rather than requiring
    each route to also remember to add it just for that one form).
    """
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
    retest_count = db.execute(
        select(func.count())
        .select_from(Exposure)
        .where(
            Exposure.workspace_id == workspace.id,
            Exposure.status == ExposureStatus.FIX_REPORTED,
        )
    ).scalar_one()
    switchable = db.execute(
        select(Workspace)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(
            WorkspaceMembership.user_id == membership.user_id,
            WorkspaceMembership.role != WorkspaceRole.CLIENT,
        )
        .order_by(Workspace.name)
    ).scalars()
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
        "csrf_token": get_csrf_token(request),
        "current_user": membership.user,
        "current_role": membership.role,
        "is_admin": role_at_least(membership.role, WorkspaceRole.ADMIN),
        "is_analyst": role_at_least(membership.role, WorkspaceRole.ANALYST),
        "is_org_admin": membership.user.is_org_admin,
        "demo_mode": request.app.state.settings.demo_mode,
        "project_client": workspace.client,
        "switchable_projects": list(switchable),
        "nav_retest_count": retest_count,
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
