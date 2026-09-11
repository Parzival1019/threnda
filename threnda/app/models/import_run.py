"""ImportRun — an auditable record of a single ingestion.

Every ingestion (even a failed one) is tracked so repeated imports of the
same source can be compared later (Milestone 3's differential engine).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

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

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
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

    workspace: Mapped[Workspace] = relationship(back_populates="import_runs")
    source: Mapped[Source] = relationship(back_populates="import_runs")
    findings: Mapped[list[Finding]] = relationship(back_populates="import_run")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"ImportRun(id={self.id!r}, filename={self.filename!r}, status={self.status!r})"
