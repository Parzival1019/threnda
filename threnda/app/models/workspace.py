"""Workspace — the multi-tenancy boundary, shown in the UI as a *project*.

Every other table is scoped to a workspace, and every access check runs
through ``WorkspaceMembership``. For a consultancy, one workspace is one
client engagement ("ügyfélprojekt"): ``client_id`` groups it under its
``Client``. ``client_id`` is nullable so workspaces created before clients
existed (or a consultancy's own internal workspace) keep working unchanged.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from threnda.app.db.base import Base
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.asset import Asset
    from threnda.app.models.candidate_finding import CandidateFinding
    from threnda.app.models.client import Client
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
    # RESTRICT (the default): a client that still has projects can't be
    # deleted out from under them.
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id"), nullable=True, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    client: Mapped[Client | None] = relationship(back_populates="projects")
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
