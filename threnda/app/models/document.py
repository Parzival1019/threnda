"""Document — a locally-extracted PDF/text file, the input to the
Milestone 6 candidate-finding framework.

See docs/pentest-and-ai.md. The original uploaded bytes are never
persisted — only the derived plain text — matching every other importer
in this codebase (CSV/JSON/SARIF bytes aren't stored either); a
``Document`` belongs to a ``Source`` the same way any other upload does,
so re-processing correlates through the same workspace/source scoping as
everything else.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
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
from threnda.app.models.enums import DocumentContentType, DocumentStatus, sa_enum
from threnda.app.models.mixins import utcnow
from threnda.app.models.types import UTCDateTime

if TYPE_CHECKING:
    from threnda.app.models.candidate_finding import CandidateFinding
    from threnda.app.models.source import Source
    from threnda.app.models.workspace import Workspace


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["sources.workspace_id", "sources.id"],
            name="fk_documents_workspace_source",
            ondelete="CASCADE",
        ),
        # Lets CandidateFinding composite-FK to (workspace_id, id) — see
        # threnda.app.models.candidate_finding.
        UniqueConstraint("workspace_id", "id", name="uq_documents_workspace_id_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Sanitized display name only — never used for filesystem access, same
    # rule as threnda.app.services.ingestion.upload.sanitize_filename.
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[DocumentContentType] = mapped_column(
        sa_enum(DocumentContentType), nullable=False
    )
    file_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[DocumentStatus] = mapped_column(sa_enum(DocumentStatus), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The full extracted text (capped — see
    # threnda.app.services.documents.extraction), kept so candidate
    # excerpts/provenance can be re-displayed without re-uploading the
    # original file. Never the original bytes.
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="documents")
    source: Mapped[Source] = relationship(
        primaryjoin=(
            "and_(foreign(Document.source_id) == Source.id, "
            "Document.workspace_id == Source.workspace_id)"
        ),
    )
    candidates: Mapped[list[CandidateFinding]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        primaryjoin=(
            "and_(foreign(CandidateFinding.document_id) == Document.id, "
            "CandidateFinding.workspace_id == Document.workspace_id)"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Document(id={self.id!r}, filename={self.filename!r}, status={self.status!r})"
