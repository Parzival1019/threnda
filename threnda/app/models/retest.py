"""Retest — one recorded verification of a finding's fix by a pentester.

The only path into ``ExposureStatus.RESOLVED`` ("verified closed") — see
``threnda.app.services.retests``. Rows are append-only.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from threnda.app.db.base import Base
from threnda.app.models.enums import ExposureStatus, RetestOutcome, sa_enum
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.exposure import Exposure


class Retest(Base):
    __tablename__ = "retests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "exposure_id"],
            ["exposures.workspace_id", "exposures.id"],
            name="fk_retests_workspace_exposure",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exposure_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    outcome: Mapped[RetestOutcome] = mapped_column(sa_enum(RetestOutcome), nullable=False)
    # Status the exposure was in when the retest was recorded.
    previous_status: Mapped[ExposureStatus] = mapped_column(sa_enum(ExposureStatus), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    performed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    performed_by_username: Mapped[str] = mapped_column(String(64), nullable=False)
    performed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    exposure: Mapped[Exposure] = relationship(
        primaryjoin=(
            "and_(foreign(Retest.exposure_id) == Exposure.id, "
            "Retest.workspace_id == Exposure.workspace_id)"
        ),
        viewonly=True,
    )
