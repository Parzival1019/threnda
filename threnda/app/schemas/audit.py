from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from threnda.app.models.enums import AuditEventType


class AuditEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int | None
    actor_username: str | None
    event_type: AuditEventType
    target_type: str | None
    target_id: str | None
    detail: dict[str, Any] | None
    ip_address: str | None
    created_at: datetime
