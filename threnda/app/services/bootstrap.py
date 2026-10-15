"""One-time instance bootstrap shared by CLI and Desktop onboarding."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app.models.enums import WorkspaceRole
from threnda.app.models.user import User
from threnda.app.models.workspace import Workspace
from threnda.app.services.memberships import add_member
from threnda.app.services.user_accounts import create_user

DEFAULT_WORKSPACE_NAME = "Default"


class InstanceAlreadyInitializedError(RuntimeError):
    """Raised when bootstrap is attempted after the first account exists."""


def has_accounts(db: Session) -> bool:
    return db.execute(select(func.count()).select_from(User)).scalar_one() > 0


def create_first_admin(db: Session, username: str, password: str) -> tuple[User, Workspace]:
    """Create the first admin and workspace, without committing the transaction."""
    if has_accounts(db):
        raise InstanceAlreadyInitializedError("An account already exists.")

    user = create_user(db, username, password, actor=None)
    # The first account is the consultancy's owner: it can manage clients
    # and create projects (docs/security.md, "Consultancy admins").
    user.is_org_admin = True
    workspace = db.execute(select(Workspace).order_by(Workspace.id).limit(1)).scalar_one_or_none()
    if workspace is None:
        workspace = Workspace(name=DEFAULT_WORKSPACE_NAME)
        db.add(workspace)
        db.flush()
    add_member(db, workspace, user, WorkspaceRole.ADMIN, actor=user)
    return user, workspace
