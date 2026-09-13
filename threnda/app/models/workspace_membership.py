"""WorkspaceMembership — grants one user one role in one workspace.

This is the sole source of authorization for workspace-scoped access
(Milestone 7): a request's ``workspace_id`` is never itself sufficient to
read or write anything, only a matching membership row for the
authenticated session's user is. See docs/security.md, "Authorization".
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from threnda.app.db.base import Base
from threnda.app.models.enums import WorkspaceRole, sa_enum
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.user import User
    from threnda.app.models.workspace import Workspace


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_memberships_workspace_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[WorkspaceRole] = mapped_column(sa_enum(WorkspaceRole), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"WorkspaceMembership(workspace_id={self.workspace_id!r}, "
            f"user_id={self.user_id!r}, role={self.role!r})"
        )
