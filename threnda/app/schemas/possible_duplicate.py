from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from threnda.app.models.enums import PossibleDuplicateStatus


class PossibleDuplicateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    finding_id: int
    candidate_exposure_id: int
    similarity_score: float
    status: PossibleDuplicateStatus
    created_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
