"""Read access to the administrative audit log — admin only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_db, require_admin
from threnda.app.models.audit_log import AuditLogEntry
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.schemas.audit import AuditEntryRead
from threnda.app.schemas.common import Page

router = APIRouter(prefix="/audit-log", tags=["audit-log"])


@router.get("", response_model=Page[AuditEntryRead])
def list_audit_log(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Page[AuditEntryRead]:
    stmt = select(AuditLogEntry).where(AuditLogEntry.workspace_id == membership.workspace_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(stmt.order_by(AuditLogEntry.id.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return Page[AuditEntryRead](
        items=[AuditEntryRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
