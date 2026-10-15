"""Attachment — an evidence file attached to one finding.

File content is stored in the database itself (``content``), not on the
local filesystem: a single ``pg_dump``/SQLite backup then captures every
piece of evidence together with the findings it belongs to, and there is no
second storage location whose permissions, backups or path handling could
drift. The per-file size cap (``THRENDA_ATTACHMENT_MAX_BYTES``) keeps this
reasonable — see docs/attachments.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
)
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from threnda.app.db.base import Base
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.exposure import Exposure


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "exposure_id"],
            ["exposures.workspace_id", "exposures.id"],
            name="fk_attachments_workspace_exposure",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exposure_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Sanitized display name only — never used to build a filesystem path.
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Always one of the allowlisted types in threnda.app.services.attachments,
    # derived from the file's verified content, never the client's header.
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Deferred: list views never load file bodies.
    content: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_by_username: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    exposure: Mapped[Exposure] = relationship(
        primaryjoin=(
            "and_(foreign(Attachment.exposure_id) == Exposure.id, "
            "Attachment.workspace_id == Exposure.workspace_id)"
        ),
        viewonly=True,
    )
