from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from threnda.app.models.enums import ExposureHistoryEventType, ExposureStatus, SeverityLevel


class ExposureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    canonical_fingerprint: str
    title: str
    description: str | None
    severity: SeverityLevel
    risk_score: int
    status: ExposureStatus
    owner: str | None
    acknowledged: bool
    remediation_target_date: date | None
    remediation: str | None
    first_seen: datetime
    last_seen: datetime
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExposureUpdate(BaseModel):
    """Partial update payload for ``PATCH /api/v1/exposures/{id}``.

    Every field is optional; only fields explicitly present in the request
    body are applied (see ``exclude_unset`` in the router). Unknown fields
    are rejected rather than silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    status: ExposureStatus | None = None
    owner: str | None = None
    acknowledged: bool | None = None
    remediation_target_date: date | None = None
    remediation: str | None = None
    note: str | None = Field(
        default=None,
        description="Optional free-text note recorded in the exposure's audit history.",
    )


class ExposureHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exposure_id: int
    event_type: ExposureHistoryEventType
    previous_value: str | None
    new_value: str | None
    actor: str | None
    timestamp: datetime
