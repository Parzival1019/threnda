"""ImportRun — an auditable record of a single ingestion.

Every ingestion (even a failed one) is tracked so repeated imports of the
same source can be compared later (Milestone 3's differential engine).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
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
from threnda.app.models.enums import ImportStatus, sa_enum
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.finding import Finding
    from threnda.app.models.source import Source
    from threnda.app.models.workspace import Workspace


class ImportRun(Base):
    __tablename__ = "import_runs"
    __table_args__ = (
        # Includes source_id (not just workspace_id, id) so Finding can
        # composite-FK to (workspace_id, source_id, id) and thereby enforce
        # that a Finding's import_run actually belongs to the same Source
        # the Finding itself references. See Finding.import_run.
        UniqueConstraint(
            "workspace_id", "source_id", "id", name="uq_import_runs_workspace_id_source_id_id"
        ),
        # Composite, not a plain FK on source_id: a single-column FK only
        # guarantees the Source row exists, not that it belongs to this
        # ImportRun's workspace. Requires uq_sources_workspace_id_id.
        ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["sources.workspace_id", "sources.id"],
            name="fk_import_runs_workspace_source",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[ImportStatus] = mapped_column(
        sa_enum(ImportStatus), nullable=False, default=ImportStatus.PENDING
    )
    total_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Milestone 3 import differential breakdown — see
    # docs/community-v0.1-spec.md, "IMPORT DIFFERENTIALS". Every
    # successfully-normalized record falls into exactly one of new/
    # unchanged/updated/reopened; resolved_findings counts existing findings
    # for this source that this import did *not* report (and so are not
    # among the incoming records at all — they're counted separately, not
    # a subset of successful_records).
    new_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unchanged_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolved_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reopened_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # workspace_id is a plain single-column FK to Workspace: this
    # relationship owns its synchronization normally.
    workspace: Mapped[Workspace] = relationship(back_populates="import_runs")
    # source_id/workspace_id are a composite FK to Source. foreign() marks
    # only source_id as managed by this relationship, so assigning
    # `import_run.source = x` can never silently rewrite workspace_id — see
    # the identical pattern (and the "why") on Finding.source.
    source: Mapped[Source] = relationship(
        primaryjoin=(
            "and_(foreign(ImportRun.source_id) == Source.id, "
            "ImportRun.workspace_id == Source.workspace_id)"
        ),
        back_populates="import_runs",
    )
    findings: Mapped[list[Finding]] = relationship(
        back_populates="import_run",
        primaryjoin=(
            "and_(foreign(Finding.import_run_id) == ImportRun.id, "
            "Finding.workspace_id == ImportRun.workspace_id, "
            "Finding.source_id == ImportRun.source_id)"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"ImportRun(id={self.id!r}, filename={self.filename!r}, status={self.status!r})"
