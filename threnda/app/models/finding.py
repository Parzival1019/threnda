"""Finding — a single normalized, source-reported security finding.

Users mostly interact with ``Exposure``, the correlated view; ``Finding`` is
the raw material an ``Exposure`` is built from. A finding is never deleted,
even once its exposure is resolved — see docs/community-v0.1-spec.md,
"IMPORT DIFFERENTIALS".
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
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
    __table_args__ = (
        # Plain single-column FKs on source_id/import_run_id/asset_id/
        # exposure_id only guarantee the referenced row exists — not that it
        # belongs to this Finding's workspace. A Finding in workspace A could
        # otherwise point at a Source/Asset/Exposure/ImportRun that actually
        # belongs to workspace B. Composite FKs against each table's
        # (workspace_id, id) unique constraint make the database itself
        # reject that, including via raw SQL.
        #
        # SET NULL is not used here even for the nullable columns
        # (asset_id/exposure_id/import_run_id): SQLite's ON DELETE SET NULL
        # on a composite key nulls *every* column in it, including
        # workspace_id — which is NOT NULL on this table, so it would fail
        # its own constraint. These links use the database default (no
        # action / RESTRICT-like): deleting an Asset/Exposure/ImportRun that
        # a Finding still references is blocked until the reference is
        # cleared at the application level. For a security tool that is the
        # safer failure mode anyway — it prevents silently orphaning
        # evidence, and the spec requires findings/history to never be
        # deleted regardless.
        ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["sources.workspace_id", "sources.id"],
            name="fk_findings_workspace_source",
            ondelete="CASCADE",
        ),
        # Three columns, not two: an ImportRun always runs against exactly
        # one Source, so a Finding produced by that run must reference the
        # *same* Source the run itself used — not merely a Source in the
        # same workspace. Requires import_runs to have a matching
        # (workspace_id, source_id, id) unique constraint.
        ForeignKeyConstraint(
            ["workspace_id", "source_id", "import_run_id"],
            ["import_runs.workspace_id", "import_runs.source_id", "import_runs.id"],
            name="fk_findings_workspace_source_import_run",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_findings_workspace_asset",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "exposure_id"],
            ["exposures.workspace_id", "exposures.id"],
            name="fk_findings_workspace_exposure",
        ),
        # The import differential engine (Milestone 3) identifies "the same
        # finding, seen again" by looking up (workspace_id, source_id,
        # fingerprint) — this constraint makes that identity a real database
        # guarantee, not just an application convention: two rows for the
        # same source can never silently coexist with the same fingerprint.
        UniqueConstraint(
            "workspace_id",
            "source_id",
            "fingerprint",
            name="uq_findings_workspace_source_fingerprint",
        ),
        # Lets PossibleDuplicate composite-FK to (workspace_id, id) — see
        # threnda.app.models.possible_duplicate.
        UniqueConstraint("workspace_id", "id", name="uq_findings_workspace_id_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Layer-1 deterministic dedup key (see docs/community-v0.1-spec.md,
    # "DEDUPLICATION"). Populated by the ingestion pipeline in Milestone 2/3;
    # not yet computed by anything in Milestone 1.
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Not part of the spec's minimum field list, added here because the
    # Milestone 3 import differential engine needs to know which import last
    # observed a given finding. Nullable: a finding is not required to be
    # tied to a specific run at the model level.
    import_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source_finding_id: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[SeverityLevel] = mapped_column(sa_enum(SeverityLevel), nullable=False)
    cvss_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cve: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    cwe: Mapped[str | None] = mapped_column(String(50), nullable=True)
    asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    exposure_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    remediation: Mapped[str | None] = mapped_column(Text, nullable=True)
    references: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    # Set by the Milestone 3 differential engine when a repeat import of
    # this finding's source no longer reports it; cleared (back to `None`)
    # if it reappears in a later import ("reopened"). Distinct from
    # `Exposure.status` (RESOLVED is a user workflow decision there) — this
    # tracks what the *source data* says, not what a user decided to do
    # about it.
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False
    )

    # workspace_id is a plain single-column FK to Workspace, so this
    # relationship owns its synchronization normally: it's the only
    # relationship allowed to write Finding.workspace_id.
    workspace: Mapped[Workspace] = relationship(back_populates="findings")

    # The four relationships below all share workspace_id as part of a
    # composite join condition. By default SQLAlchemy would treat
    # workspace_id as a column *every one* of them is allowed to write on
    # flush — so assigning e.g. `finding.source = some_source` would
    # silently overwrite `finding.workspace_id` with `some_source.workspace_id`,
    # even if it had already been set correctly. `foreign()` marks only the
    # specific id column as the thing this relationship manages; workspace_id
    # stays a plain join predicate that only `Finding.workspace` may write.
    source: Mapped[Source] = relationship(
        primaryjoin=(
            "and_(foreign(Finding.source_id) == Source.id, "
            "Finding.workspace_id == Source.workspace_id)"
        ),
        back_populates="findings",
    )
    import_run: Mapped[ImportRun | None] = relationship(
        primaryjoin=(
            "and_(foreign(Finding.import_run_id) == ImportRun.id, "
            "Finding.workspace_id == ImportRun.workspace_id, "
            "Finding.source_id == ImportRun.source_id)"
        ),
        back_populates="findings",
    )
    asset: Mapped[Asset | None] = relationship(
        primaryjoin=(
            "and_(foreign(Finding.asset_id) == Asset.id, "
            "Finding.workspace_id == Asset.workspace_id)"
        ),
        back_populates="findings",
    )
    exposure: Mapped[Exposure | None] = relationship(
        primaryjoin=(
            "and_(foreign(Finding.exposure_id) == Exposure.id, "
            "Finding.workspace_id == Exposure.workspace_id)"
        ),
        back_populates="findings",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Finding(id={self.id!r}, title={self.title!r}, severity={self.severity!r})"
