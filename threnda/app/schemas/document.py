from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from threnda.app.models.enums import (
    CandidateFindingStatus,
    DocumentContentType,
    DocumentStatus,
    SeverityLevel,
)


class CandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    document_id: int
    status: CandidateFindingStatus
    extracted_title: str
    extracted_severity: SeverityLevel | None
    excerpt: str
    page_number: int | None
    line_start: int | None
    line_end: int | None
    confidence: float
    validation_issues: list[str]
    ai_assisted: bool
    ai_summary: str | None
    ai_model: str | None
    resulting_finding_id: int | None
    created_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    source_id: int
    filename: str
    content_type: DocumentContentType
    file_hash: str
    status: DocumentStatus
    error_message: str | None
    page_count: int | None
    created_at: datetime


class DocumentDetailRead(DocumentRead):
    extracted_text: str
    candidates: list[CandidateRead]


class CandidateDecision(BaseModel):
    """The accept decision body. ``actor`` is no longer a client-supplied
    field — see docs/security.md, "Authorization" — it is always the
    authenticated session's username.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    severity: SeverityLevel | None = None
