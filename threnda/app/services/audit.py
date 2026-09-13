"""The single write path for the administrative audit log.

Every administrative/security-sensitive action goes through
:func:`record_audit_event` rather than constructing ``AuditLogEntry`` rows
directly, so the "never a secret in `detail`" rule has exactly one place to
be enforced (and tested) instead of being a convention every call site has
to remember. See docs/security.md, "Auditing".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from threnda.app.models.audit_log import AuditLogEntry
from threnda.app.models.enums import AuditEventType
from threnda.app.models.user import User
from threnda.app.models.workspace import Workspace

# Any detail key containing one of these substrings is rejected outright —
# a defensive backstop against a call site accidentally passing a secret
# through, not the primary control (the primary control is simply never
# putting a secret in `detail` to begin with).
_FORBIDDEN_KEY_SUBSTRINGS = (
    "password",
    "secret",
    "token",
    "session",
    "authorization",
    "cookie",
    "hash",
)


class UnsafeAuditDetailError(ValueError):
    """Raised when ``detail`` appears to contain a secret-shaped field."""


def _check_detail_safety(detail: dict[str, Any] | None) -> None:
    if not detail:
        return
    for key in detail:
        lowered = key.lower()
        if any(bad in lowered for bad in _FORBIDDEN_KEY_SUBSTRINGS):
            raise UnsafeAuditDetailError(
                f"Refusing to write audit detail field {key!r} — looks like it may hold a secret."
            )


def record_audit_event(
    db: Session,
    *,
    event_type: AuditEventType,
    actor: User | None,
    workspace: Workspace | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    detail: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLogEntry:
    """Write one immutable audit log entry.

    ``actor`` is ``None`` for events with no authenticated user yet (e.g. a
    failed login against an unknown username). Raises
    ``UnsafeAuditDetailError`` if ``detail`` contains a key that looks like
    it might hold a secret — see the module docstring.
    """
    _check_detail_safety(detail)
    entry = AuditLogEntry(
        workspace_id=workspace.id if workspace is not None else None,
        actor_user_id=actor.id if actor is not None else None,
        actor_username=actor.username if actor is not None else None,
        event_type=event_type,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        detail=detail,
        ip_address=ip_address,
    )
    db.add(entry)
    db.flush()
    return entry
