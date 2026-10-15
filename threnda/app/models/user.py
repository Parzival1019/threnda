"""User — a local account.

Users are global (not workspace-scoped); per-workspace access is granted
through :class:`~threnda.app.models.workspace_membership.WorkspaceMembership`
rows. See docs/security.md, "Accounts and roles".
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from threnda.app.db.base import Base
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.client import Client
    from threnda.app.models.user_session import UserSession
    from threnda.app.models.workspace_membership import WorkspaceMembership


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stored already normalized (stripped + lowercased) by
    # threnda.app.services.auth — see that module's docstring.
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Consultancy-level administrator: may manage clients and create
    # projects. This grants *no* access to any project's data by itself —
    # that still comes only from a WorkspaceMembership row (docs/security.md,
    # "No superuser").
    is_org_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set only for client-side users: binds the account to one Client, and
    # threnda.app.services.memberships refuses to grant such an account any
    # role other than ``client``, or any project of a different client.
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id"), nullable=True, index=True
    )
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False
    )

    client: Mapped[Client | None] = relationship(back_populates="users")
    memberships: Mapped[list[WorkspaceMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"User(id={self.id!r}, username={self.username!r})"
