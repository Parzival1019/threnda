from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WebhookRead(BaseModel):
    """Never includes the signing secret — see docs/security.md, "Webhooks"."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    description: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime
    last_triggered_at: datetime | None
    last_status_code: int | None
    last_error: str | None


class WebhookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    description: str | None = Field(default=None, max_length=200)


class WebhookCreated(BaseModel):
    endpoint: WebhookRead
    secret: str = Field(
        description="Shown exactly once. Copy it into the receiving system now — "
        "Threnda cannot display it again."
    )


class WebhookEnabledUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class WebhookTriggerResult(BaseModel):
    ok: bool
    status_code: int | None
    error: str | None
