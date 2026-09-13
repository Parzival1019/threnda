"""CSV/JSON export of a workspace's exposures. See docs/exports.md."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from threnda.app.api.deps import get_db, require_analyst
from threnda.app.models.enums import AuditEventType
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.services.audit import record_audit_event
from threnda.app.services.exports import iter_csv_lines, iter_json_array

router = APIRouter(prefix="/exports", tags=["exports"])


@router.get("/exposures")
def export_exposures(
    request: Request,
    format: str = Query(default="json", pattern="^(json|csv)$"),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    record_audit_event(
        db,
        event_type=AuditEventType.EXPORT_DOWNLOADED,
        actor=membership.user,
        workspace=membership.workspace,
        target_type="exposures",
        detail={"format": format},
        ip_address=request.client.host if request.client else None,
    )
    if format == "csv":
        return StreamingResponse(
            iter_csv_lines(db, membership.workspace_id),
            media_type="text/csv",
            headers={"content-disposition": "attachment; filename=exposures.csv"},
        )
    return StreamingResponse(
        iter_json_array(db, membership.workspace_id),
        media_type="application/json",
        headers={"content-disposition": "attachment; filename=exposures.json"},
    )
