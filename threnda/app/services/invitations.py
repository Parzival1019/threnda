"""One-time invitation links: an admin invites, the invitee sets a password.

The plaintext token exists only in the link shown once to the inviting
admin (Threnda does not send email — the admin passes the link on through
their own channel; see docs/security.md, "Invitations"). Only its SHA-256
hash is stored. A link is single-use, expires (``invitation_ttl_hours``),
and can be revoked. Accepting creates the account and the project
membership in one transaction; a ``client``-role invitation binds the new
account to the project's client.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.enums import AuditEventType, WorkspaceRole
from threnda.app.models.mixins import utcnow
from threnda.app.models.user import User
from threnda.app.models.user_invitation import UserInvitation
from threnda.app.models.workspace import Workspace
from threnda.app.services.audit import record_audit_event
from threnda.app.services.auth import validate_username
from threnda.app.services.memberships import add_member
from threnda.app.services.user_accounts import create_user


class InvitationError(ValueError):
    """A user-correctable problem creating an invitation."""


class InvalidInvitationError(Exception):
    """The link is unknown, used, revoked or expired.

    Deliberately one error for all four cases — the accept page must not
    reveal which one applies to someone holding a guessed or stale link.
    """


@dataclass(frozen=True)
class IssuedInvitation:
    invitation: UserInvitation
    token: str


def hash_invitation_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_invitation(
    db: Session,
    workspace: Workspace,
    *,
    username: str,
    role: WorkspaceRole,
    actor: User,
    ttl_hours: int,
) -> IssuedInvitation:
    normalized = validate_username(username)
    if db.execute(select(User.id).where(User.username == normalized)).scalar_one_or_none():
        raise InvitationError(
            f"An account named {normalized!r} already exists — add it to the project directly "
            "instead of inviting it."
        )
    if role == WorkspaceRole.CLIENT and workspace.client_id is None:
        raise InvitationError(
            "This project is not linked to a client, so it cannot have client users."
        )

    # A newer invitation for the same person/project supersedes older ones.
    for stale in db.execute(
        select(UserInvitation).where(
            UserInvitation.workspace_id == workspace.id,
            UserInvitation.username == normalized,
            UserInvitation.accepted_at.is_(None),
            UserInvitation.revoked_at.is_(None),
        )
    ).scalars():
        stale.revoked_at = utcnow()

    token = secrets.token_urlsafe(32)
    invitation = UserInvitation(
        token_hash=hash_invitation_token(token),
        username=normalized,
        workspace_id=workspace.id,
        role=role,
        created_by_user_id=actor.id,
        expires_at=utcnow() + timedelta(hours=ttl_hours),
    )
    db.add(invitation)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.INVITATION_CREATED,
        actor=actor,
        workspace=workspace,
        target_type="invitation",
        target_id=invitation.id,
        detail={"username": normalized, "role": role.value},
    )
    return IssuedInvitation(invitation=invitation, token=token)


def find_valid_invitation(db: Session, token: str | None) -> UserInvitation:
    if not token or len(token) > 200:
        raise InvalidInvitationError
    invitation = db.execute(
        select(UserInvitation).where(UserInvitation.token_hash == hash_invitation_token(token))
    ).scalar_one_or_none()
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= utcnow()
    ):
        raise InvalidInvitationError
    return invitation


def accept_invitation(db: Session, token: str, password: str) -> User:
    """Create the account + membership. Raises ``InvalidInvitationError``, or
    ``WeakPasswordError``/``UsernameTakenError`` from account creation."""
    invitation = find_valid_invitation(db, token)
    workspace = invitation.workspace
    user = create_user(db, invitation.username, password, actor=None)
    if invitation.role == WorkspaceRole.CLIENT:
        user.client_id = workspace.client_id
    add_member(db, workspace, user, invitation.role, actor=user)
    invitation.accepted_at = utcnow()
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.INVITATION_ACCEPTED,
        actor=user,
        workspace=workspace,
        target_type="invitation",
        target_id=invitation.id,
        detail={"username": user.username, "role": invitation.role.value},
    )
    return user


def revoke_invitation(db: Session, invitation: UserInvitation, *, actor: User) -> None:
    if invitation.accepted_at is not None or invitation.revoked_at is not None:
        return
    invitation.revoked_at = utcnow()
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.INVITATION_REVOKED,
        actor=actor,
        workspace=invitation.workspace,
        target_type="invitation",
        target_id=invitation.id,
        detail={"username": invitation.username},
    )


def pending_invitations(db: Session, workspace_id: int) -> list[UserInvitation]:
    now = utcnow()
    return list(
        db.execute(
            select(UserInvitation)
            .where(
                UserInvitation.workspace_id == workspace_id,
                UserInvitation.accepted_at.is_(None),
                UserInvitation.revoked_at.is_(None),
                UserInvitation.expires_at > now,
            )
            .order_by(UserInvitation.created_at.desc())
        ).scalars()
    )
