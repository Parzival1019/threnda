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
    new_findings: int
    unchanged_findings: int
    updated_findings: int
    resolved_findings: int
    reopened_findings: int


class ImportRowErrorRead(BaseModel):
    """One record that failed to normalize. Not persisted — returned only
    in the upload response, since ``ImportRun`` stores counts, not detail.
    """

    index: int
    message: str


class ImportUploadResult(BaseModel):
    """Response for a processed upload — including a partially-failed one.

    ``row_errors`` is always present (possibly empty) whenever the file
    itself was parseable, even if every single record failed; that is a
    processed import with ``successful_records == 0``, not a rejected
    upload — the distinction the caller should notice is `import_run.status`
    plus the record counts, not the HTTP status code alone.
    """

    import_run: ImportRunRead
    row_errors: list[ImportRowErrorRead]
