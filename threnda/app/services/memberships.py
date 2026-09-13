"""Workspace membership management — the admin-only "manage members" surface.

Every mutation here is audited (see docs/security.md, "Auditing") and
guards against a workspace being left with zero admins, which would
otherwise lock everyone out of managing it (Community has no superuser
escape hatch by design — see docs/security.md, "No superuser").
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app.models.enums import AuditEventType, WorkspaceRole
from threnda.app.models.user import User
from threnda.app.models.workspace import Workspace
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.services.audit import record_audit_event

_ROLE_ORDER = {WorkspaceRole.VIEWER: 0, WorkspaceRole.ANALYST: 1, WorkspaceRole.ADMIN: 2}


def role_at_least(role: WorkspaceRole, minimum: WorkspaceRole) -> bool:
    """Whether ``role`` grants at least the privilege of ``minimum``."""
    return _ROLE_ORDER[role] >= _ROLE_ORDER[minimum]


class LastAdminError(ValueError):
    """Raised when an action would leave a workspace with no admin member."""


def _admin_count(db: Session, workspace_id: int) -> int:
    return db.execute(
        select(func.count())
        .select_from(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.role == WorkspaceRole.ADMIN,
        )
    ).scalar_one()


def add_member(
    db: Session,
    workspace: Workspace,
    user: User,
    role: WorkspaceRole,
    *,
    actor: User,
) -> WorkspaceMembership:
    membership = WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=role)
    db.add(membership)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.MEMBER_ADDED,
        actor=actor,
        workspace=workspace,
        target_type="user",
        target_id=user.id,
        detail={"username": user.username, "role": role.value},
    )
    return membership


def update_member_role(
    db: Session,
    membership: WorkspaceMembership,
    role: WorkspaceRole,
    *,
    actor: User,
) -> WorkspaceMembership:
    if (
        membership.role == WorkspaceRole.ADMIN
        and role != WorkspaceRole.ADMIN
        and _admin_count(db, membership.workspace_id) <= 1
    ):
        raise LastAdminError("Cannot demote the last admin of a workspace.")
    previous_role = membership.role
    membership.role = role
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.MEMBER_ROLE_CHANGED,
        actor=actor,
        workspace=membership.workspace,
        target_type="user",
        target_id=membership.user_id,
        detail={
            "username": membership.user.username,
            "previous_role": previous_role.value,
            "new_role": role.value,
        },
    )
    return membership


def remove_member(db: Session, membership: WorkspaceMembership, *, actor: User) -> None:
    if membership.role == WorkspaceRole.ADMIN and _admin_count(db, membership.workspace_id) <= 1:
        raise LastAdminError("Cannot remove the last admin of a workspace.")
    workspace = membership.workspace
    username = membership.user.username
    user_id = membership.user_id
    role = membership.role
    db.delete(membership)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.MEMBER_REMOVED,
        actor=actor,
        workspace=workspace,
        target_type="user",
        target_id=user_id,
        detail={"username": username, "role": role.value},
    )
