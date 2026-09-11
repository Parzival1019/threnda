from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from threnda.app.models.enums import AssetEnvironment, AssetType


class AssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    canonical_key: str
    name: str
    asset_type: AssetType
    environment: AssetEnvironment
    meta: dict[str, Any]
    first_seen: datetime
    last_seen: datetime
