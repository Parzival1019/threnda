"""ExposureHistory — the audit trail of every meaningful workflow change."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from threnda.app.db.base import Base
from threnda.app.models.enums import ExposureHistoryEventType, sa_enum
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.exposure import Exposure


class ExposureHistory(Base):
    __tablename__ = "exposure_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    exposure_id: Mapped[int] = mapped_column(
        ForeignKey("exposures.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[ExposureHistoryEventType] = mapped_column(
        sa_enum(ExposureHistoryEventType), nullable=False
    )
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-text identity of who/what made the change — always the
    # authenticated session's username since Milestone 7 (see
    # threnda.app.services.exposures.apply_exposure_update's callers).
    actor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, nullable=False, index=True
    )

    exposure: Mapped[Exposure] = relationship(back_populates="history")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"ExposureHistory(id={self.id!r}, event_type={self.event_type!r})"
