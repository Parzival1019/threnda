from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from threnda.app.models.enums import WorkspaceRole


class MembershipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    role: WorkspaceRole
    created_at: datetime
    username: str = Field(description="Denormalized for display; populated from the joined user.")


class MemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    # Only used the first time this username is seen on this instance —
    # required to create the account. Adding an *existing* username to a
    # second workspace ignores this field entirely.
    password: str | None = Field(default=None, min_length=1, max_length=256)
    role: WorkspaceRole = WorkspaceRole.VIEWER


class MemberRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: WorkspaceRole
