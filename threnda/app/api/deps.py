"""Shared FastAPI dependencies for the JSON API.

Authorization model (Milestone 7): every workspace-scoped route depends on
``require_min_role(...)`` (or, for a read that any member may perform,
``require_viewer``), never on a bare ``workspace_id`` query parameter
alone. ``workspace_id`` still appears as a request parameter — a user can
belong to more than one workspace — but it is no longer *itself* the
authority: it only grants access once combined with a
``WorkspaceMembership`` row for the authenticated session's user. A
``workspace_id`` that doesn't exist and one that exists but the caller
isn't a member of are answered identically (``404``), so neither response
lets a caller enumerate other workspaces' existence. See
docs/security.md, "Authorization".
"""

from __future__ import annotations

from collections.abc import Callable, Generator

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.db.session import iter_session
from threnda.app.models.enums import WorkspaceRole
from threnda.app.models.user import User
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.services.auth import validate_session
from threnda.app.services.memberships import role_at_least
from threnda.app.web.cookies import SESSION_COOKIE_NAME


def get_db(request: Request) -> Generator[Session, None, None]:
    """Yield a database session bound to the current app's sessionmaker.

    The sessionmaker lives on ``app.state`` (set in ``create_app()``)
    rather than a module global, so each application instance — including
    one built per test with its own temporary database — gets its own
    sessions.
    """
    yield from iter_session(request.app.state.sessionmaker)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Resolve the authenticated user from the session cookie, or ``401``.

    Also records the live session on ``request.state.session`` so route
    handlers that need to revoke the current session (logout) don't have
    to re-look it up.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    session = validate_session(db, token)
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    request.state.session = session
    return session.user


def get_workspace_membership(
    workspace_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceMembership:
    """The caller's membership in ``workspace_id``, or ``404``.

    ``404`` (never ``403``) whether the workspace doesn't exist at all or
    the caller simply isn't a member of it — see the module docstring.
    """
    membership = db.execute(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user.id,
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return membership


def require_min_role(
    minimum: WorkspaceRole,
) -> Callable[[WorkspaceMembership], WorkspaceMembership]:
    """Build a dependency requiring at least ``minimum`` role in the workspace.

    Roles are hierarchical (admin > analyst > viewer,
    ``threnda.app.services.memberships.role_at_least``); this is safe to
    use as ``403`` (rather than the ``404`` membership itself uses) because
    reaching this check already proves the caller *is* a member — refusing
    an insufficiently-privileged member does not reveal anything about
    other users or workspaces.
    """

    def _dependency(
        membership: WorkspaceMembership = Depends(get_workspace_membership),
    ) -> WorkspaceMembership:
        if not role_at_least(membership.role, minimum):
            raise HTTPException(status_code=403, detail="Insufficient permissions for this action.")
        return membership

    return _dependency


# ``client`` is the lowest role: only routes that explicitly opt in with
# ``require_client`` (the client-facing finding/comment/attachment routes)
# accept a client-side user, and those routes filter out internal-only
# content themselves. Any workspace staff member may read; analysts additionally operate
# findings/imports/documents/possible-duplicates/exports; only admins
# manage membership and webhooks.
require_client = require_min_role(WorkspaceRole.CLIENT)
require_viewer = require_min_role(WorkspaceRole.VIEWER)
require_analyst = require_min_role(WorkspaceRole.ANALYST)
require_admin = require_min_role(WorkspaceRole.ADMIN)
