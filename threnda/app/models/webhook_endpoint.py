"""WebhookEndpoint — a single admin-configured outbound delivery target.

Generic delivery only (see docs/webhooks.md): Community signs and POSTs a
JSON payload to ``url`` when an admin explicitly triggers a delivery. There
is no automatic per-event delivery and no vendor-specific integration.

``secret_ciphertext`` holds the signing secret encrypted at rest (Fernet,
keyed from ``THRENDA_SECRET_KEY`` — see ``threnda.app.services.crypto``),
not hashed: unlike a password or session token, the plaintext secret must
be recoverable to sign each outgoing delivery, so it cannot be stored as a
one-way hash. It is never included in any API response after creation and
never logged.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from threnda.app.db.base import Base
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.user import User
    from threnda.app.models.workspace import Workspace


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False
    )
    last_triggered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="webhook_endpoints")
    created_by: Mapped[User | None] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"WebhookEndpoint(id={self.id!r}, workspace_id={self.workspace_id!r})"
