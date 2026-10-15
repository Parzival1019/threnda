from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from threnda.app.models.enums import WorkspaceRole


class ClientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    notes: str | None = None


class ClientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    notes: str | None
    created_at: datetime


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    client_id: int | None
    description: str | None
    created_at: datetime


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    role: WorkspaceRole


class InvitationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    workspace_id: int
    role: WorkspaceRole
    created_at: datetime
    expires_at: datetime


class IssuedInvitationRead(InvitationRead):
    """Returned once, at creation: the only time the link is ever available."""

    invite_url: str


class CsvProblemRead(BaseModel):
    line: int
    column: str | None
    message: str


class FindingsCsvImportRead(BaseModel):
    ok: bool
    problems: list[CsvProblemRead]
    total_rows: int
    import_run_id: int | None
    new: int
    updated: int
    unchanged: int
    reopened: int
    not_in_this_import: int
