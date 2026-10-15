"""Consultancy pages: all-projects overview, clients/projects, the pentest
findings CSV import, the status report, and invitation acceptance.

``/overview`` and ``/clients`` deliberately don't depend on a current
project (a brand-new consultancy admin has none yet), so they render with
the plain layout instead of the per-project sidebar.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from starlette.responses import RedirectResponse, Response

from threnda.app.api.deps import get_db
from threnda.app.models.client import Client
from threnda.app.models.enums import AuditEventType
from threnda.app.models.exposure import Exposure
from threnda.app.models.user import User
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.services.audit import record_audit_event
from threnda.app.services.auth import WeakPasswordError
from threnda.app.services.findings_csv import TEMPLATE_COLUMNS, TEMPLATE_CSV, import_template_csv
from threnda.app.services.ingestion.upload import sanitize_filename
from threnda.app.services.invitations import (
    InvalidInvitationError,
    accept_invitation,
    find_valid_invitation,
)
from threnda.app.services.projects import (
    ProjectError,
    create_client,
    create_project,
    memberships_for,
    project_stats,
)
from threnda.app.services.reports import SEVERITY_ORDER, STATUS_ORDER, build_project_report
from threnda.app.services.user_accounts import UsernameTakenError
from threnda.app.web.csrf import apply_csrf_cookie, get_csrf_token, verify_csrf
from threnda.app.web.deps import (
    build_shell_context,
    get_current_membership,
    get_current_user,
    require_analyst,
    templates,
)
from threnda.app.web.filters import ROLE_LABELS

router = APIRouter(tags=["web-projects"])


def _plain_context(request: Request, user: User, **extra: Any) -> dict[str, Any]:
    return {
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "demo_mode": request.app.state.settings.demo_mode,
        **extra,
    }


def _render(
    request: Request, template: str, context: dict[str, Any], status_code: int = 200
) -> Response:
    response = templates.TemplateResponse(request, template, context, status_code=status_code)
    apply_csrf_cookie(request, response, context["csrf_token"])
    return response


# --- overview ---------------------------------------------------------------


@router.get("/overview")
def overview(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    staff = memberships_for(db, user, client_side=False)
    if not staff and memberships_for(db, user, client_side=True):
        return RedirectResponse(url="/portal", status_code=302)
    stats = [project_stats(db, m.workspace, m.role) for m in staff]
    totals = {
        key: sum(getattr(s, key) for s in stats)
        for key in ("active", "critical_active", "overdue", "awaiting_retest", "verified_closed")
    }
    return _render(
        request,
        "projects/overview.html",
        _plain_context(request, user, active_nav="overview", stats=stats, totals=totals),
    )


# --- clients and projects ---------------------------------------------------


def _require_org_admin(user: User) -> None:
    if not user.is_org_admin:
        raise HTTPException(status_code=403, detail="Consultancy admin only.")


def _clients_page(
    request: Request, db: Session, user: User, *, error: str | None = None, status_code: int = 200
) -> Response:
    clients = (
        db.execute(select(Client).options(selectinload(Client.projects)).order_by(Client.name))
        .scalars()
        .all()
    )
    member_ids = {m.workspace_id for m in memberships_for(db, user, client_side=False)}
    return _render(
        request,
        "projects/clients.html",
        _plain_context(
            request,
            user,
            active_nav="clients",
            clients=clients,
            member_ids=member_ids,
            error=error,
        ),
        status_code=status_code,
    )


@router.get("/clients")
def clients_page(
    request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    _require_org_admin(user)
    return _clients_page(request, db, user)


@router.post("/clients")
def create_client_form(
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(default=""),
    notes: str = Form(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    _require_org_admin(user)
    try:
        create_client(db, name=name, notes=notes, actor=user)
    except ProjectError as exc:
        return _clients_page(request, db, user, error=str(exc), status_code=409)
    return RedirectResponse(url="/clients", status_code=303)


@router.post("/clients/{client_id}/projects")
def create_project_form(
    request: Request,
    client_id: int,
    csrf_token: str = Form(...),
    name: str = Form(default=""),
    description: str = Form(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    _require_org_admin(user)
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    try:
        project = create_project(db, client=client, name=name, description=description, actor=user)
    except ProjectError as exc:
        return _clients_page(request, db, user, error=str(exc), status_code=409)
    return RedirectResponse(url=f"/?workspace_id={project.id}", status_code=303)


# --- pentest findings CSV ---------------------------------------------------


@router.get("/findings-import/template.csv")
def findings_template(_user: User = Depends(get_current_user)) -> Response:
    return Response(
        content=TEMPLATE_CSV,
        media_type="text/csv",
        headers={"content-disposition": "attachment; filename=threnda-findings-template.csv"},
    )


@router.get("/findings-import")
def findings_import_page(
    request: Request,
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    return _render(
        request,
        "projects/findings_import.html",
        {
            "active_nav": "findings_import",
            "columns": TEMPLATE_COLUMNS,
            "result": None,
            **build_shell_context(request, db, membership.workspace, membership),
        },
    )


@router.post("/findings-import")
async def findings_import_submit(
    request: Request,
    csrf_token: str = Form(...),
    file: UploadFile = File(...),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    raw = await file.read()
    filename = sanitize_filename(file.filename)
    result = import_template_csv(
        db, membership.workspace, raw=raw, filename=filename, actor=membership.user.username
    )
    return _render(
        request,
        "projects/findings_import.html",
        {
            "active_nav": "findings_import",
            "columns": TEMPLATE_COLUMNS,
            "result": result,
            "filename": filename,
            **build_shell_context(request, db, membership.workspace, membership),
        },
        status_code=200 if result.ok else 422,
    )


# --- status report -----------------------------------------------------------


@router.get("/report")
def report_page(
    request: Request,
    membership: WorkspaceMembership = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> Response:
    workspace = membership.workspace
    report = build_project_report(db, workspace)
    record_audit_event(
        db,
        event_type=AuditEventType.REPORT_VIEWED,
        actor=membership.user,
        workspace=workspace,
        target_type="workspace",
        target_id=workspace.id,
    )

    def finding_link(exposure: Exposure) -> str:
        return f"/exposures?workspace_id={workspace.id}&open={exposure.id}"

    shell = build_shell_context(request, db, workspace, membership)
    return _render(
        request,
        "projects/report.html",
        {
            "active_nav": "report",
            "report": report,
            "severity_order": SEVERITY_ORDER,
            "status_order": STATUS_ORDER,
            "finding_link": finding_link,
            "export_url": (
                f"/api/v1/exports/exposures?workspace_id={workspace.id}&format=csv"
                if shell["is_analyst"]
                else None
            ),
            **shell,
        },
    )


# --- invitation acceptance (no session required) -----------------------------


def _invite_page(
    request: Request,
    *,
    token: str,
    username: str | None,
    role_label: str | None,
    project: str | None,
    error: str | None,
    status_code: int = 200,
) -> Response:
    csrf = get_csrf_token(request)
    return _render(
        request,
        "invite.html",
        {
            "csrf_token": csrf,
            "token": token,
            "username": username,
            "role_label": role_label,
            "project": project,
            "error": error,
        },
        status_code=status_code,
    )


_INVALID_INVITE = "This invitation link is invalid, has already been used, or has expired."


@router.get("/invite/{token}")
def invite_form(request: Request, token: str, db: Session = Depends(get_db)) -> Response:
    try:
        invitation = find_valid_invitation(db, token)
    except InvalidInvitationError:
        return _invite_page(
            request,
            token="",
            username=None,
            role_label=None,
            project=None,
            error=_INVALID_INVITE,
            status_code=404,
        )
    return _invite_page(
        request,
        token=token,
        username=invitation.username,
        role_label=ROLE_LABELS[invitation.role],
        project=invitation.workspace.name,
        error=None,
    )


@router.post("/invite/{token}")
def invite_accept(
    request: Request,
    token: str,
    csrf_token: str = Form(...),
    password: str = Form(default=""),
    password_confirm: str = Form(default=""),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    try:
        invitation = find_valid_invitation(db, token)
    except InvalidInvitationError:
        return _invite_page(
            request,
            token="",
            username=None,
            role_label=None,
            project=None,
            error=_INVALID_INVITE,
            status_code=404,
        )

    def again(message: str) -> Response:
        return _invite_page(
            request,
            token=token,
            username=invitation.username,
            role_label=ROLE_LABELS[invitation.role],
            project=invitation.workspace.name,
            error=message,
            status_code=422,
        )

    if password != password_confirm:
        return again("The two passwords do not match.")
    try:
        accept_invitation(db, token, password)
    except WeakPasswordError as exc:
        db.rollback()
        return again(str(exc))
    except (UsernameTakenError, InvalidInvitationError):
        db.rollback()
        return again(_INVALID_INVITE)
    return RedirectResponse(url="/login?notice=account-created", status_code=303)
