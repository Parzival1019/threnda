from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from threnda.app.models.enums import SourceType


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    name: str
    source_type: SourceType
    meta: dict[str, Any]
    created_at: datetime
