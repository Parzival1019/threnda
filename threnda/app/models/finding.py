"""Finding — a single normalized, source-reported security finding.

Users mostly interact with ``Exposure``, the correlated view; ``Finding`` is
the raw material an ``Exposure`` is built from. A finding is never deleted,
even once its exposure is resolved — see docs/community-v0.1-spec.md,
"IMPORT DIFFERENTIALS".
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from threnda.app.db.base import Base
from threnda.app.models.enums import SeverityLevel, sa_enum
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.asset import Asset
    from threnda.app.models.exposure import Exposure
    from threnda.app.models.import_run import ImportRun
    from threnda.app.models.source import Source
    from threnda.app.models.workspace import Workspace


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Layer-1 deterministic dedup key (see docs/community-v0.1-spec.md,
    # "DEDUPLICATION"). Populated by the ingestion pipeline in Milestone 2/3;
    # not yet computed by anything in Milestone 1.
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Not part of the spec's minimum field list, added here because the
    # Milestone 3 import differential engine needs to know which import last
    # observed a given finding. Nullable: a finding is not required to be
    # tied to a specific run at the model level.
    import_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_finding_id: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[SeverityLevel] = mapped_column(sa_enum(SeverityLevel), nullable=False)
    cvss_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cve: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    cwe: Mapped[str | None] = mapped_column(String(50), nullable=True)
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    exposure_id: Mapped[int | None] = mapped_column(
        ForeignKey("exposures.id", ondelete="SET NULL"), nullable=True, index=True
    )
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    remediation: Mapped[str | None] = mapped_column(Text, nullable=True)
    references: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False
    )

    workspace: Mapped[Workspace] = relationship(back_populates="findings")
    source: Mapped[Source] = relationship(back_populates="findings")
    import_run: Mapped[ImportRun | None] = relationship(back_populates="findings")
    asset: Mapped[Asset | None] = relationship(back_populates="findings")
    exposure: Mapped[Exposure | None] = relationship(back_populates="findings")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Finding(id={self.id!r}, title={self.title!r}, severity={self.severity!r})"
