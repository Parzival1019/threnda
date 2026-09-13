"""CandidateFinding — a heuristic (or AI-assisted) section of a ``Document``
that might describe a real security finding, pending mandatory human
review.

See docs/pentest-and-ai.md, "Mandatory human review": a ``CandidateFinding``
never becomes a ``Finding`` except through one explicit accept action
(``threnda.app.services.documents.review.accept_candidate``), which routes
through the normal import differential/correlation/risk-scoring pipeline
exactly like any other upload. Confidence/validation issues are a
heuristic aid, never an authoritative judgement — see
``threnda.app.services.documents.section_detection``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    and_,  # noqa: F401 - referenced by name inside string primaryjoin exprs below
)
from sqlalchemy.orm import (
    Mapped,
    foreign,  # noqa: F401 - referenced by name inside string primaryjoin exprs
    mapped_column,
    relationship,
)

from threnda.app.db.base import Base
from threnda.app.models.enums import CandidateFindingStatus, SeverityLevel, sa_enum
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.document import Document
    from threnda.app.models.finding import Finding
    from threnda.app.models.workspace import Workspace


class CandidateFinding(Base):
    __tablename__ = "candidate_findings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            name="fk_candidate_findings_workspace_document",
            ondelete="CASCADE",
        ),
        # Set only once a candidate is accepted (see review.py); the
        # candidate row is never deleted, matching the "findings/history
        # are never deleted" rule already applied everywhere else.
        ForeignKeyConstraint(
            ["workspace_id", "resulting_finding_id"],
            ["findings.workspace_id", "findings.id"],
            name="fk_candidate_findings_workspace_finding",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[CandidateFindingStatus] = mapped_column(
        sa_enum(CandidateFindingStatus), nullable=False, default=CandidateFindingStatus.PENDING
    )
    # Heuristic (or AI-suggested) extraction — always editable/overridable
    # by the human reviewer at accept time, never treated as final.
    extracted_title: Mapped[str] = mapped_column(String(500), nullable=False)
    extracted_severity: Mapped[SeverityLevel | None] = mapped_column(
        sa_enum(SeverityLevel), nullable=True
    )
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    # Provenance: which page (PDF) and/or line range (either format) the
    # excerpt came from in the source document.
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    validation_issues: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # True when threnda.app.services.ai produced this candidate (or
    # enriched it) — always additionally labelled in the API/UI, never
    # implied by anything else on the row.
    ai_assisted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resulting_finding_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Placeholder identity until authentication exists — same caveat as
    # ExposureHistory.actor since Milestone 1.
    resolved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="candidate_findings")
    document: Mapped[Document] = relationship(
        back_populates="candidates",
        primaryjoin=(
            "and_(foreign(CandidateFinding.document_id) == Document.id, "
            "CandidateFinding.workspace_id == Document.workspace_id)"
        ),
    )
    resulting_finding: Mapped[Finding | None] = relationship(
        primaryjoin=(
            "and_(foreign(CandidateFinding.resulting_finding_id) == Finding.id, "
            "CandidateFinding.workspace_id == Finding.workspace_id)"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"CandidateFinding(id={self.id!r}, status={self.status!r})"
