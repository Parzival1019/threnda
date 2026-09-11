"""Source — where a batch of findings came from."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from threnda.app.db.base import Base
from threnda.app.models.enums import SourceType, sa_enum
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.finding import Finding
    from threnda.app.models.import_run import ImportRun
    from threnda.app.models.workspace import Workspace


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(sa_enum(SourceType), nullable=False)
    # Spec field name is "metadata"; the Python/API attribute is "meta"
    # because SQLAlchemy's DeclarativeBase reserves `metadata` on every
    # mapped class. The database column itself is still named "metadata".
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="sources")
    import_runs: Mapped[list[ImportRun]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    findings: Mapped[list[Finding]] = relationship(back_populates="source")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Source(id={self.id!r}, name={self.name!r}, type={self.source_type!r})"
