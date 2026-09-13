"""AuditLogEntry — an immutable record of a security-sensitive action.

Written for administrative/security-sensitive events only (account
creation, membership changes, login outcomes, webhook configuration,
exports) — never for routine finding/exposure edits, which already have
their own audit trail (``ExposureHistory``). ``detail`` must never contain
a password, session token, or webhook secret; see
``threnda.app.services.audit`` for the single write path that enforces
this. See docs/security.md, "Auditing".
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from threnda.app.db.base import Base
from threnda.app.models.enums import AuditEventType, sa_enum
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.user import User
    from threnda.app.models.workspace import Workspace


class AuditLogEntry(Base):
    __tablename__ = "audit_log_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable: some events (a login attempt for an unknown username, a
    # user-level action with no workspace context) have no workspace.
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Denormalized snapshot so the log stays readable even if actor_user_id
    # is later null (deleted account) — usernames are immutable in
    # Community, so this never drifts from the live value while the
    # account exists.
    actor_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type: Mapped[AuditEventType] = mapped_column(sa_enum(AuditEventType), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    workspace: Mapped[Workspace | None] = relationship()
    actor: Mapped[User | None] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"AuditLogEntry(id={self.id!r}, event_type={self.event_type!r})"
