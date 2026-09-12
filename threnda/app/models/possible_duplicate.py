"""PossibleDuplicate — a POSSIBLE-confidence correlation candidate.

See docs/community-v0.1-spec.md, "DEDUPLICATION": ``EXACT``/``HIGH``
confidence matches correlate automatically (``Finding.exposure_id`` is set
directly, with a ``FINDING_CORRELATED`` ``ExposureHistory`` entry); a
``POSSIBLE`` match is never auto-merged — it's recorded here instead, for a
human to merge or keep separate. There is deliberately no row for
EXACT/HIGH matches: they never needed a decision in the first place.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    and_,  # noqa: F401 - referenced by name inside string primaryjoin exprs below
)
from sqlalchemy.orm import (
    Mapped,
    foreign,  # noqa: F401 - referenced by name inside string primaryjoin exprs
    mapped_column,
    relationship,
)

from threnda.app.db.base import Base
from threnda.app.models.enums import PossibleDuplicateStatus, sa_enum
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.exposure import Exposure
    from threnda.app.models.finding import Finding
    from threnda.app.models.workspace import Workspace


class PossibleDuplicate(Base):
    __tablename__ = "possible_duplicates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "finding_id"],
            ["findings.workspace_id", "findings.id"],
            name="fk_possible_duplicates_workspace_finding",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "candidate_exposure_id"],
            ["exposures.workspace_id", "exposures.id"],
            name="fk_possible_duplicates_workspace_candidate_exposure",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The new/updated finding that triggered this candidate.
    finding_id: Mapped[int] = mapped_column(nullable=False, index=True)
    # The existing exposure it might be a duplicate of.
    candidate_exposure_id: Mapped[int] = mapped_column(nullable=False, index=True)
    # Layer 2 (fuzzy) similarity score in [0.0, 1.0] — see
    # threnda.app.services.dedup.similarity. Kept for sorting/review context;
    # not itself the decision.
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[PossibleDuplicateStatus] = mapped_column(
        sa_enum(PossibleDuplicateStatus), nullable=False, default=PossibleDuplicateStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Placeholder identity until authentication exists — same caveat as
    # ExposureHistory.actor.
    resolved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="possible_duplicates")
    finding: Mapped[Finding] = relationship(
        primaryjoin=(
            "and_(foreign(PossibleDuplicate.finding_id) == Finding.id, "
            "PossibleDuplicate.workspace_id == Finding.workspace_id)"
        ),
    )
    candidate_exposure: Mapped[Exposure] = relationship(
        primaryjoin=(
            "and_(foreign(PossibleDuplicate.candidate_exposure_id) == Exposure.id, "
            "PossibleDuplicate.workspace_id == Exposure.workspace_id)"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"PossibleDuplicate(id={self.id!r}, finding_id={self.finding_id!r}, "
            f"candidate_exposure_id={self.candidate_exposure_id!r}, status={self.status!r})"
        )
