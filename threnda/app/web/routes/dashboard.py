"""Minimal dashboard — a handful of real aggregate counts, not a
visualization showpiece (see docs/implementation-plan.md, Milestone 5).
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_db
from threnda.app.models.enums import ExposureStatus, SeverityLevel
from threnda.app.models.exposure import Exposure
from threnda.app.models.mixins import utcnow
from threnda.app.models.workspace import Workspace
from threnda.app.web.deps import build_shell_context, get_current_workspace, templates

router = APIRouter(tags=["web-dashboard"])


def _count(db: Session, workspace_id: int, *conditions: ColumnElement[bool]) -> int:
    stmt = (
        select(func.count())
        .select_from(Exposure)
        .where(Exposure.workspace_id == workspace_id, *conditions)
    )
    return db.execute(stmt).scalar_one()


@router.get("/")
def dashboard(
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> object:
    now = utcnow()
    week_ago = now - timedelta(days=7)
    today = now.date()

    not_resolved = Exposure.status != ExposureStatus.RESOLVED

    open_count = _count(db, workspace.id, Exposure.status == ExposureStatus.OPEN)
    critical_count = _count(
        db, workspace.id, Exposure.severity == SeverityLevel.CRITICAL, not_resolved
    )
    high_count = _count(db, workspace.id, Exposure.severity == SeverityLevel.HIGH, not_resolved)
    overdue_count = _count(
        db,
        workspace.id,
        not_resolved,
        Exposure.remediation_target_date.is_not(None),
        Exposure.remediation_target_date < today,
    )
    new_this_week = _count(db, workspace.id, Exposure.first_seen >= week_ago)
    resolved_this_week = _count(
        db,
        workspace.id,
        Exposure.status == ExposureStatus.RESOLVED,
        Exposure.resolved_at.is_not(None),
        Exposure.resolved_at >= week_ago,
    )

    # Averaged in Python rather than SQL: there is no portable SQLite/
    # PostgreSQL expression for "days between two timestamps" (SQLite's
    # `julianday()` has no PostgreSQL equivalent, and CLAUDE.md rules out
    # SQLite-only constructs) — see docs/implementation-plan.md.
    open_first_seen = (
        db.execute(
            select(Exposure.first_seen).where(Exposure.workspace_id == workspace.id, not_resolved)
        )
        .scalars()
        .all()
    )
    avg_age_days = None
    if open_first_seen:
        total_seconds = sum((now - seen).total_seconds() for seen in open_first_seen)
        avg_age_days = round(total_seconds / len(open_first_seen) / 86400)

    context = {
        "active_nav": "dashboard",
        "open_count": open_count,
        "critical_count": critical_count,
        "high_count": high_count,
        "overdue_count": overdue_count,
        "new_this_week": new_this_week,
        "resolved_this_week": resolved_this_week,
        "avg_age_days": avg_age_days,
        **build_shell_context(db, workspace),
    }
    return templates.TemplateResponse(request, "dashboard.html", context)
