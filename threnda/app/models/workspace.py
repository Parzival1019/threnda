"""Workspace — the multi-tenancy boundary.

Community Edition is single-user today, but every other table is scoped to
a workspace from the start so multi-workspace support later does not
require a schema migration through every table.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from threnda.app.db.base import Base
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.asset import Asset
    from threnda.app.models.candidate_finding import CandidateFinding
    from threnda.app.models.document import Document
    from threnda.app.models.exposure import Exposure
    from threnda.app.models.finding import Finding
    from threnda.app.models.import_run import ImportRun
    from threnda.app.models.possible_duplicate import PossibleDuplicate
    from threnda.app.models.source import Source
    from threnda.app.models.webhook_endpoint import WebhookEndpoint
    from threnda.app.models.workspace_membership import WorkspaceMembership


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    sources: Mapped[list[Source]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    import_runs: Mapped[list[ImportRun]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    assets: Mapped[list[Asset]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    findings: Mapped[list[Finding]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    exposures: Mapped[list[Exposure]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    possible_duplicates: Mapped[list[PossibleDuplicate]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    documents: Mapped[list[Document]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    candidate_findings: Mapped[list[CandidateFinding]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    memberships: Mapped[list[WorkspaceMembership]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    webhook_endpoints: Mapped[list[WebhookEndpoint]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Workspace(id={self.id!r}, name={self.name!r})"
