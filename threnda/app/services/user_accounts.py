"""Local account creation — shared by the bootstrap CLI and the admin UI.

There is no self-registration anywhere in Community: the very first
account is created once by ``threnda.app.cli.bootstrap_admin`` (see
docs/deployment.md), and every account after that is created by an
existing workspace admin through the members settings page. Both paths go
through :func:`create_user` so the username/password policy
(``threnda.app.services.auth``) and the audit trail are enforced exactly
once.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.enums import AuditEventType
from threnda.app.models.user import User
from threnda.app.services.audit import record_audit_event
from threnda.app.services.auth import hash_password, validate_password_strength, validate_username


class UsernameTakenError(ValueError):
    """Raised when the requested username already belongs to another account."""


def create_user(
    db: Session,
    username: str,
    password: str,
    *,
    actor: User | None,
) -> User:
    """Validate, hash, and persist a brand-new local account.

    Raises ``InvalidUsernameError``/``WeakPasswordError``
    (``threnda.app.services.auth``) or ``UsernameTakenError``. ``actor`` is
    ``None`` only for the one-time bootstrap path, which has no
    authenticated user yet.
    """
    normalized = validate_username(username)
    validate_password_strength(password, username=normalized)

    existing = db.execute(select(User).where(User.username == normalized)).scalar_one_or_none()
    if existing is not None:
        raise UsernameTakenError(f"Username {normalized!r} is already taken.")

    user = User(username=normalized, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.USER_CREATED,
        actor=actor,
        target_type="user",
        target_id=user.id,
        detail={"username": normalized},
    )
    return user
