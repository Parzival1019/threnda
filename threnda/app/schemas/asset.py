from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator

from threnda.app.models.enums import AssetCriticality, AssetEnvironment, AssetType


class AssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    canonical_key: str
    name: str
    asset_type: AssetType
    environment: AssetEnvironment
    internet_facing: bool | None
    business_criticality: AssetCriticality
    meta: dict[str, Any]
    first_seen: datetime
    last_seen: datetime


class AssetUpdate(BaseModel):
    """Partial, manual update for the Milestone 4 risk-scoring inputs.

    Both fields are entirely manual — see ``threnda.app.models.asset`` for
    why no importer can set either. ``business_criticality`` cannot be
    cleared to ``null`` (the column is ``NOT NULL``, defaulting to
    ``unknown`` — omit the field to leave it unchanged, same rule as
    ``ExposureUpdate.status``); ``internet_facing`` *can* be set to
    ``null`` to explicitly revert to "not known".
    """

    model_config = ConfigDict(extra="forbid")

    internet_facing: bool | None = None
    business_criticality: AssetCriticality | None = None

    @field_validator("business_criticality")
    @classmethod
    def _reject_explicit_null(cls, value: object, info: ValidationInfo) -> object:
        if value is None:
            raise ValueError(
                f"{info.field_name} cannot be set to null; omit the field to leave it unchanged"
            )
        return value
