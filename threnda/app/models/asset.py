"""Asset — the generic thing a finding is found on."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
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
from threnda.app.models.enums import AssetCriticality, AssetEnvironment, AssetType, sa_enum
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.finding import Finding
    from threnda.app.models.workspace import Workspace


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "canonical_key", name="uq_assets_workspace_canonical_key"),
        # Lets Finding composite-FK to (workspace_id, id) — see Finding.
        UniqueConstraint("workspace_id", "id", name="uq_assets_workspace_id_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Stable identity for correlating the "same" asset across imports, e.g.
    # a normalized hostname, IP, URL or repository path. Unique per workspace.
    canonical_key: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    asset_type: Mapped[AssetType] = mapped_column(
        sa_enum(AssetType), nullable=False, default=AssetType.UNKNOWN
    )
    environment: Mapped[AssetEnvironment] = mapped_column(
        sa_enum(AssetEnvironment), nullable=False, default=AssetEnvironment.UNKNOWN
    )
    # Both of the fields below are Milestone 4 risk-scoring inputs (see
    # threnda.app.services.risk_scoring) that no importer can populate —
    # no public CSV/JSON/SARIF format carries either concept, and
    # "manual business criticality" is explicitly manual in
    # docs/community-v0.1-spec.md. Nullable/unknown-by-default rather than
    # a plain boolean/best-guess: the scoring engine treats "unknown"
    # (never asked) differently from "known false" (asked, and it isn't).
    #
    # `deferred=True` for the same reason as `Finding.exploit_evidence`:
    # migration 32832b96f2c3's backfill step (and the live
    # `threnda.app.services.dedup.correlation` code it reuses) selects
    # whole `Asset`/`Finding` entities from a point in the upgrade sequence
    # before *this* migration has added these columns to the table —
    # deferring keeps them out of that implicit column list. Explicitly
    # accessed (and lazy-loaded) wherever risk scoring actually needs them.
    internet_facing: Mapped[bool | None] = mapped_column(Boolean, nullable=True, deferred=True)
    business_criticality: Mapped[AssetCriticality] = mapped_column(
        sa_enum(AssetCriticality), nullable=False, default=AssetCriticality.UNKNOWN, deferred=True
    )
    # See threnda.app.models.source for why this is "meta" and not "metadata".
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="assets")
    findings: Mapped[list[Finding]] = relationship(
        back_populates="asset",
        primaryjoin=(
            "and_(foreign(Finding.asset_id) == Asset.id, "
            "Finding.workspace_id == Asset.workspace_id)"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Asset(id={self.id!r}, canonical_key={self.canonical_key!r})"
