"""The client portal — the only UI a ``client``-role user ever reaches.

Every route resolves the caller's membership with :func:`_client_membership`,
which accepts *only* a ``client`` membership in the requested project. A
consultancy user opening a portal URL is sent back to the consultancy UI,
and a project the caller has no client membership in is a plain 404 —
identical to one that doesn't exist. Every read goes through the
``visible_*`` helpers in ``threnda.app.services.collaboration``, so internal
comments/attachments/history never reach this module's templates.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, undefer
from starlette.responses import RedirectResponse, Response

from threnda.app.api.deps import get_db
from threnda.app.models.enums import AuditEventType, ExposureStatus, SeverityLevel, WorkspaceRole
from threnda.app.models.exposure import Exposure
from threnda.app.models.mixins import utcnow
from threnda.app.models.user import User
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.services.audit import record_audit_event
from threnda.app.services.collaboration import (
    CollaborationError,
    add_attachment,
    add_comment,
    client_set_status,
    visible_attachments,
    visible_comments,
    visible_history,
)
from threnda.app.services.exposures import ACTIVE_STATUSES, InvalidStatusTransitionError
from threnda.app.services.projects import memberships_for, project_stats
from threnda.app.services.reports import (
    SEVERITY_ORDER,
    STATUS_ORDER,
    build_project_report,
    is_overdue,
)
from threnda.app.web.csrf import apply_csrf_cookie, get_csrf_token, verify_csrf
from threnda.app.web.deps import get_current_user, templates

router = APIRouter(prefix="/portal", tags=["web-portal"])


def _client_membership(db: Session, user: User, workspace_id: int) -> WorkspaceMembership:
    membership = db.execute(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user.id,
            WorkspaceMembership.role == WorkspaceRole.CLIENT,
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return membership


def _get_exposure(db: Session, workspace_id: int, exposure_id: int) -> Exposure:
    exposure = db.get(Exposure, exposure_id)
    if exposure is None or exposure.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Finding not found")
    return exposure


def _render(
    request: Request,
    user: User,
    template: str,
    context: dict[str, Any],
    status_code: int = 200,
) -> Response:
    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request,
        template,
        {
            "current_user": user,
            "csrf_token": token,
            "demo_mode": request.app.state.settings.demo_mode,
            **context,
        },
        status_code=status_code,
    )
    apply_csrf_cookie(request, response, token)
    return response


@router.get("")
def portal_home(
    request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    client_memberships = memberships_for(db, user, client_side=True)
    if not client_memberships:
        # A consultancy user has no portal; send them to their own UI.
        return RedirectResponse(url="/overview", status_code=302)
    if len(client_memberships) == 1:
        return RedirectResponse(
            url=f"/portal/projects/{client_memberships[0].workspace_id}", status_code=302
        )
    stats = [project_stats(db, m.workspace, m.role) for m in client_memberships]
    return _render(request, user, "portal/home.html", {"stats": stats})


@router.get("/projects/{workspace_id}")
def portal_project(
    request: Request,
    workspace_id: int,
    status: ExposureStatus | None = None,
    severity: SeverityLevel | None = None,
    q: str | None = None,
    overdue: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    membership = _client_membership(db, user, workspace_id)
    workspace = membership.workspace
    stmt = (
        select(Exposure)
        .where(Exposure.workspace_id == workspace.id)
        .options(undefer(Exposure.affected_system))
    )
    if status is not None:
        stmt = stmt.where(Exposure.status == status)
    if severity is not None:
        stmt = stmt.where(Exposure.severity == severity)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Exposure.title.ilike(pattern),
                Exposure.description.ilike(pattern),
                Exposure.affected_system.ilike(pattern),
                Exposure.owner.ilike(pattern),
            )
        )
    today = utcnow().date()
    if overdue:
        stmt = stmt.where(
            Exposure.status.in_(ACTIVE_STATUSES),
            Exposure.remediation_target_date.is_not(None),
            Exposure.remediation_target_date < today,
        )
    sev_rank = {s: i for i, s in enumerate(SEVERITY_ORDER)}
    exposures = sorted(
        db.execute(stmt).scalars().all(),
        key=lambda e: (e.status not in ACTIVE_STATUSES, sev_rank[e.severity], e.id),
    )
    total = db.execute(
        select(func.count()).select_from(Exposure).where(Exposure.workspace_id == workspace.id)
    ).scalar_one()
    return _render(
        request,
        user,
        "portal/project.html",
        {
            "workspace": workspace,
            "stats": project_stats(db, workspace, membership.role),
            "rows": [(e, is_overdue(e, today)) for e in exposures],
            "total": total,
            "status": status,
            "severity": severity,
            "q": q or "",
            "overdue": overdue,
            "statuses": STATUS_ORDER,
            "severities": SEVERITY_ORDER,
        },
    )


def _finding_page(
    request: Request,
    db: Session,
    user: User,
    membership: WorkspaceMembership,
    exposure: Exposure,
    *,
    error: str | None = None,
    notice: str | None = None,
    status_code: int = 200,
) -> Response:
    findings_remediation = next((f.remediation for f in exposure.findings if f.remediation), None)
    return _render(
        request,
        user,
        "portal/finding.html",
        {
            "workspace": membership.workspace,
            "exposure": exposure,
            "remediation": exposure.remediation or findings_remediation,
            "overdue": is_overdue(exposure, utcnow().date()),
            "comments": visible_comments(db, exposure, membership.role),
            "attachments": visible_attachments(db, exposure, membership.role),
            "history": list(reversed(visible_history(exposure, membership.role))),
            "can_report_fix": exposure.status
            in {ExposureStatus.OPEN, ExposureStatus.ACKNOWLEDGED, ExposureStatus.IN_PROGRESS},
            "can_mark_in_progress": exposure.status
            in {ExposureStatus.OPEN, ExposureStatus.ACKNOWLEDGED},
            "max_attachment_bytes": request.app.state.settings.attachment_max_bytes,
            "error": error,
            "notice": notice,
        },
        status_code=status_code,
    )


#: Fixed confirmation messages, selected by key after a POST -> redirect, so
#: reloading the page never re-submits a form and the query string is never
#: echoed into the page.
_NOTICES = {
    "fixed": "Thank you — the finding is marked as fixed. Your consultant will retest it.",
    "in_progress": "The finding is marked as in progress.",
    "comment": "Comment posted.",
    "evidence": "Evidence uploaded.",
}


def _back_to_finding(workspace_id: int, exposure_id: int, notice: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/portal/projects/{workspace_id}/findings/{exposure_id}?notice={notice}",
        status_code=303,
    )


@router.get("/projects/{workspace_id}/findings/{exposure_id}")
def portal_finding(
    request: Request,
    workspace_id: int,
    exposure_id: int,
    notice: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    membership = _client_membership(db, user, workspace_id)
    exposure = _get_exposure(db, workspace_id, exposure_id)
    return _finding_page(request, db, user, membership, exposure, notice=_NOTICES.get(notice or ""))


@router.post("/projects/{workspace_id}/findings/{exposure_id}/status")
def portal_set_status(
    request: Request,
    workspace_id: int,
    exposure_id: int,
    csrf_token: str = Form(...),
    status: str = Form(...),
    note: str = Form(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    membership = _client_membership(db, user, workspace_id)
    exposure = _get_exposure(db, workspace_id, exposure_id)
    try:
        requested = ExposureStatus(status)
        client_set_status(db, exposure, status=requested, note=note, actor=user)
    except (ValueError, InvalidStatusTransitionError) as exc:
        db.rollback()
        exposure = _get_exposure(db, workspace_id, exposure_id)
        return _finding_page(
            request, db, user, membership, exposure, error=str(exc), status_code=422
        )
    key = "fixed" if requested == ExposureStatus.FIX_REPORTED else "in_progress"
    return _back_to_finding(workspace_id, exposure_id, key)


@router.post("/projects/{workspace_id}/findings/{exposure_id}/comments")
def portal_comment(
    request: Request,
    workspace_id: int,
    exposure_id: int,
    csrf_token: str = Form(...),
    body: str = Form(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    membership = _client_membership(db, user, workspace_id)
    exposure = _get_exposure(db, workspace_id, exposure_id)
    try:
        add_comment(db, exposure, body=body, internal=False, actor=user, actor_role=membership.role)
    except CollaborationError as exc:
        return _finding_page(
            request, db, user, membership, exposure, error=str(exc), status_code=422
        )
    return _back_to_finding(workspace_id, exposure_id, "comment")


@router.post("/projects/{workspace_id}/findings/{exposure_id}/attachments")
async def portal_attachment(
    request: Request,
    workspace_id: int,
    exposure_id: int,
    csrf_token: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    membership = _client_membership(db, user, workspace_id)
    exposure = _get_exposure(db, workspace_id, exposure_id)
    max_bytes: int = request.app.state.settings.attachment_max_bytes
    raw = await file.read(max_bytes + 1)
    try:
        add_attachment(
            db,
            exposure,
            filename=file.filename,
            raw=raw,
            internal=False,
            actor=user,
            actor_role=membership.role,
            max_bytes=max_bytes,
        )
    except CollaborationError as exc:
        return _finding_page(
            request, db, user, membership, exposure, error=str(exc), status_code=422
        )
    return _back_to_finding(workspace_id, exposure_id, "evidence")


@router.get("/projects/{workspace_id}/report")
def portal_report(
    request: Request,
    workspace_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    membership = _client_membership(db, user, workspace_id)
    record_audit_event(
        db,
        event_type=AuditEventType.REPORT_VIEWED,
        actor=user,
        workspace=membership.workspace,
        target_type="workspace",
        target_id=workspace_id,
    )

    def finding_link(exposure: Exposure) -> str:
        return f"/portal/projects/{workspace_id}/findings/{exposure.id}"

    return _render(
        request,
        user,
        "portal/report.html",
        {
            "workspace": membership.workspace,
            "report": build_project_report(db, membership.workspace),
            "severity_order": SEVERITY_ORDER,
            "status_order": STATUS_ORDER,
            "finding_link": finding_link,
            "export_url": None,
        },
    )
