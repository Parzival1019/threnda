"""Exposure — the correlated, user-facing security issue.

Multiple ``Finding`` rows (often from different sources) can point at the
same ``Exposure``. This is what users acknowledge, assign, and resolve —
see docs/community-v0.1-spec.md, "Users should mostly interact with
Exposures, not raw Findings."
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,  # noqa: F401 - referenced by name inside string primaryjoin exprs below
)
from sqlalchemy.orm import (
    Mapped,
    foreign,  # noqa: F401 - referenced by name inside string primaryjoin exprs
    mapped_column,
    relationship,
)

from threnda.app.db.base import Base
from threnda.app.models.enums import ExposureStatus, SeverityLevel, sa_enum
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.exposure_history import ExposureHistory
    from threnda.app.models.finding import Finding
    from threnda.app.models.workspace import Workspace


class Exposure(Base):
    __tablename__ = "exposures"
    __table_args__ = (
        CheckConstraint("risk_score >= 0 AND risk_score <= 100", name="risk_score_range"),
        # Lets Finding composite-FK to (workspace_id, id) — see Finding.
        UniqueConstraint("workspace_id", "id", name="uq_exposures_workspace_id_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    canonical_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[SeverityLevel] = mapped_column(sa_enum(SeverityLevel), nullable=False)
    # 0-100, rule-based and explainable (Milestone 4). Defaults to 0 until
    # the scoring engine exists.
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[ExposureStatus] = mapped_column(
        sa_enum(ExposureStatus), nullable=False, default=ExposureStatus.OPEN
    )
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    remediation_target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    remediation: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False
    )

    workspace: Mapped[Workspace] = relationship(back_populates="exposures")
    findings: Mapped[list[Finding]] = relationship(
        back_populates="exposure",
        primaryjoin=(
            "and_(foreign(Finding.exposure_id) == Exposure.id, "
            "Finding.workspace_id == Exposure.workspace_id)"
        ),
    )
    history: Mapped[list[ExposureHistory]] = relationship(
        back_populates="exposure",
        cascade="all, delete-orphan",
        order_by="ExposureHistory.timestamp",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Exposure(id={self.id!r}, title={self.title!r}, status={self.status!r})"
