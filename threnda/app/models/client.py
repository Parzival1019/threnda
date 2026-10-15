"""Client — a customer of the consultancy running this Threnda instance.

A self-hosted Threnda instance belongs to one consultancy (the "tanácsadó
szervezet"); each ``Client`` groups that consultancy's engagements
(``Workspace`` rows, shown as *projects* in the UI) for one customer.
Client-side users are bound to exactly one ``Client`` via
``User.client_id`` and can only ever be granted the ``client`` role on that
client's projects — see ``threnda.app.services.memberships`` and
docs/security.md, "Client users".
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from threnda.app.db.base import Base
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.user import User
    from threnda.app.models.workspace import Workspace


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    projects: Mapped[list[Workspace]] = relationship(
        back_populates="client", order_by="Workspace.id"
    )
    users: Mapped[list[User]] = relationship(back_populates="client")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Client(id={self.id!r}, name={self.name!r})"
