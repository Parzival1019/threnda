"""ExposureComment — a discussion message on one finding.

``is_internal`` comments are consultancy-only working notes: they are never
returned to a ``client``-role member, by the portal or by the API (see
``threnda.app.services.collaboration``). Comments are never edited or
deleted — like ``ExposureHistory``, they are part of the finding's record.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, ForeignKeyConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from threnda.app.db.base import Base
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.exposure import Exposure


class ExposureComment(Base):
    __tablename__ = "exposure_comments"
    __table_args__ = (
        # Same workspace-scoped composite-FK pattern as Finding: a comment
        # can only ever point at an exposure in its own workspace, enforced
        # by the database itself.
        ForeignKeyConstraint(
            ["workspace_id", "exposure_id"],
            ["exposures.workspace_id", "exposures.id"],
            name="fk_exposure_comments_workspace_exposure",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exposure_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    author_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Snapshot, like AuditLogEntry.actor_username — usernames are immutable.
    author_username: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    exposure: Mapped[Exposure] = relationship(
        primaryjoin=(
            "and_(foreign(ExposureComment.exposure_id) == Exposure.id, "
            "ExposureComment.workspace_id == Exposure.workspace_id)"
        ),
        viewonly=True,
    )
