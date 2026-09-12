from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

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
    body are applied (see ``exclude_unset`` in the router) — that's how
    "leave unchanged" is distinguished from "clear this field". ``status``
    and ``acknowledged`` cannot be cleared this way (the columns are
    ``NOT NULL``): omit them to leave them unchanged, but an explicit
    ``null`` is a 422, not a silent no-op or a database error. ``owner``,
    ``remediation_target_date`` and ``remediation`` *are* nullable columns,
    so an explicit ``null`` for those means "clear this field". Unknown
    fields are rejected rather than silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    status: ExposureStatus | None = None
    owner: str | None = Field(default=None, max_length=200)
    acknowledged: bool | None = None
    remediation_target_date: date | None = None
    remediation: str | None = None
    note: str | None = Field(
        default=None,
        description="Optional free-text note recorded in the exposure's audit history.",
    )

    @field_validator("status", "acknowledged")
    @classmethod
    def _reject_explicit_null(cls, value: object, info: ValidationInfo) -> object:
        if value is None:
            raise ValueError(
                f"{info.field_name} cannot be set to null; omit the field to leave it unchanged"
            )
        return value


class ExposureHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exposure_id: int
    event_type: ExposureHistoryEventType
    previous_value: str | None
    new_value: str | None
    actor: str | None
    timestamp: datetime
