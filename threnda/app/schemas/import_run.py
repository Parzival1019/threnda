from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from threnda.app.models.enums import ImportStatus


class ImportRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    source_id: int
    filename: str
    file_hash: str
    started_at: datetime
    completed_at: datetime | None
    status: ImportStatus
    total_records: int
    successful_records: int
    failed_records: int
