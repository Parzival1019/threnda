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

# CLIENT is deliberately the lowest rung: anything gated on ``viewer`` or
# above (imports, sources, documents, exports, audit, settings, ...) refuses
# a client-side user without that route having to know clients exist.
_ROLE_ORDER = {
    WorkspaceRole.CLIENT: 0,
    WorkspaceRole.VIEWER: 1,
    WorkspaceRole.ANALYST: 2,
    WorkspaceRole.ADMIN: 3,
}


def role_at_least(role: WorkspaceRole, minimum: WorkspaceRole) -> bool:
    """Whether ``role`` grants at least the privilege of ``minimum``."""
    return _ROLE_ORDER[role] >= _ROLE_ORDER[minimum]


class LastAdminError(ValueError):
    """Raised when an action would leave a workspace with no admin member."""


class ClientRoleMismatchError(ValueError):
    """Raised when a membership would break the client-user binding rules.

    A client-side account (``User.client_id`` set) may only ever hold the
    ``client`` role, and only on a project of *its own* client; a
    consultancy account (no ``client_id``) may never hold the ``client``
    role. Enforced here, the single write path for memberships, so neither
    the UI nor the API can grant a client user another client's data by
    mistake — see docs/security.md, "Client users".
    """


def validate_membership_binding(workspace: Workspace, user: User, role: WorkspaceRole) -> None:
    if user.client_id is not None:
        if role != WorkspaceRole.CLIENT:
            raise ClientRoleMismatchError(
                f"{user.username!r} is a client-side account and can only have the client role."
            )
        if workspace.client_id != user.client_id:
            raise ClientRoleMismatchError(
                f"{user.username!r} belongs to a different client than this project."
            )
    elif role == WorkspaceRole.CLIENT:
        raise ClientRoleMismatchError(
            f"{user.username!r} is a consultancy account; the client role is only for "
            "client-side accounts (invite them as a client user instead)."
        )


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
    validate_membership_binding(workspace, user, role)
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
    validate_membership_binding(membership.workspace, membership.user, role)
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
