"""Admin settings: members, webhooks, and the audit log.

Every route here requires the ``admin`` role in the current workspace —
see ``threnda.app.web.deps.require_admin``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from starlette.responses import RedirectResponse, Response

from threnda.app.api.deps import get_db
from threnda.app.api.v1.projects import invite_base_url
from threnda.app.models.audit_log import AuditLogEntry
from threnda.app.models.enums import WorkspaceRole
from threnda.app.models.user import User
from threnda.app.models.user_invitation import UserInvitation
from threnda.app.models.webhook_endpoint import WebhookEndpoint
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.services.ai import AIUnavailableError, EmbeddedAIProvider
from threnda.app.services.ai.embedded import embedded_ai_status, start_embedded_ai_install
from threnda.app.services.auth import InvalidUsernameError, WeakPasswordError, normalize_username
from threnda.app.services.demo import DemoResetRefusedError, reset_demo_data
from threnda.app.services.invitations import (
    InvitationError,
    IssuedInvitation,
    create_invitation,
    pending_invitations,
    revoke_invitation,
)
from threnda.app.services.memberships import (
    ClientRoleMismatchError,
    LastAdminError,
    add_member,
    remove_member,
    update_member_role,
)
from threnda.app.services.user_accounts import UsernameTakenError, create_user
from threnda.app.services.webhooks import (
    InvalidWebhookUrlError,
    create_webhook,
    delete_webhook,
    deliver_webhook,
    set_webhook_enabled,
)
from threnda.app.web.cookies import SESSION_COOKIE_NAME
from threnda.app.web.csrf import apply_csrf_cookie, get_csrf_token, verify_csrf
from threnda.app.web.deps import build_shell_context, require_admin, templates

router = APIRouter(prefix="/settings", tags=["web-settings"])

PAGE_SIZE = 50


@router.get("")
def settings_index(
    _membership: WorkspaceMembership = Depends(require_admin),
) -> RedirectResponse:
    return RedirectResponse(url="/settings/members", status_code=302)


def _render_members(
    request: Request,
    db: Session,
    membership: WorkspaceMembership,
    *,
    error: str | None = None,
    issued: IssuedInvitation | None = None,
    status_code: int = 200,
) -> Response:
    members = (
        db.execute(
            select(WorkspaceMembership)
            .where(WorkspaceMembership.workspace_id == membership.workspace_id)
            .options(joinedload(WorkspaceMembership.user))
            .order_by(WorkspaceMembership.id)
        )
        .scalars()
        .all()
    )
    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request,
        "settings/members.html",
        {
            "active_nav": "settings",
            "members": members,
            "roles": list(WorkspaceRole),
            "error": error,
            "csrf_token": token,
            "invitations": pending_invitations(db, membership.workspace_id),
            "issued": issued,
            "issued_url": (f"{invite_base_url(request)}/invite/{issued.token}" if issued else None),
            "invitation_ttl_hours": request.app.state.settings.invitation_ttl_hours,
            **build_shell_context(request, db, membership.workspace, membership),
        },
        status_code=status_code,
    )
    apply_csrf_cookie(request, response, token)
    return response


@router.post("/invitations")
def create_invitation_form(
    request: Request,
    csrf_token: str = Form(...),
    username: str = Form(...),
    role: str = Form(...),
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    try:
        issued = create_invitation(
            db,
            membership.workspace,
            username=username,
            role=WorkspaceRole(role),
            actor=membership.user,
            ttl_hours=request.app.state.settings.invitation_ttl_hours,
        )
    except (ValueError, InvitationError, InvalidUsernameError) as exc:
        db.rollback()
        return _render_members(request, db, membership, error=str(exc), status_code=422)
    # Rendered directly (not a redirect): the link exists only in this
    # response and is never stored or shown again.
    return _render_members(request, db, membership, issued=issued)


@router.post("/invitations/{invitation_id}/revoke")
def revoke_invitation_form(
    request: Request,
    invitation_id: int,
    csrf_token: str = Form(...),
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    invitation = db.get(UserInvitation, invitation_id)
    if invitation is None or invitation.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Invitation not found")
    revoke_invitation(db, invitation, actor=membership.user)
    return RedirectResponse(
        url=f"/settings/members?workspace_id={membership.workspace_id}", status_code=303
    )


@router.post("/demo-reset")
def demo_reset_form(
    request: Request,
    csrf_token: str = Form(...),
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    settings = request.app.state.settings
    if not settings.demo_mode or not membership.user.is_org_admin:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        reset_demo_data(db, actor_username=membership.user.username)
    except DemoResetRefusedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@router.get("/members")
def members_page(
    request: Request,
    error: str | None = None,
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    return _render_members(request, db, membership, error=error)


@router.post("/members")
def add_member_form(
    request: Request,
    csrf_token: str = Form(...),
    username: str = Form(...),
    password: str = Form(default=""),
    role: str = Form(...),
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    try:
        resolved_role = WorkspaceRole(role)
        normalized = normalize_username(username)
    except ValueError as exc:
        return _members_error(request, db, membership, str(exc))

    user = db.execute(select(User).where(User.username == normalized)).scalar_one_or_none()
    if user is None:
        if not password:
            return _members_error(
                request,
                db,
                membership,
                f"No account named {normalized!r} exists yet — a password is required to "
                "create one.",
            )
        try:
            user = create_user(db, username, password, actor=membership.user)
        except (InvalidUsernameError, WeakPasswordError, UsernameTakenError) as exc:
            db.rollback()
            return _members_error(request, db, membership, str(exc))
        if resolved_role == WorkspaceRole.CLIENT:
            # A brand-new account created directly as a client user belongs
            # to this project's client (same rule as an accepted invitation).
            user.client_id = membership.workspace.client_id

    try:
        add_member(db, membership.workspace, user, resolved_role, actor=membership.user)
        db.flush()
    except ClientRoleMismatchError as exc:
        db.rollback()
        return _members_error(request, db, membership, str(exc))
    except IntegrityError:
        db.rollback()
        return _members_error(
            request, db, membership, f"{normalized!r} is already a member of this workspace."
        )
    return RedirectResponse(
        url=f"/settings/members?workspace_id={membership.workspace_id}", status_code=303
    )


def _members_error(
    request: Request, db: Session, membership: WorkspaceMembership, error: str
) -> Response:
    return _render_members(request, db, membership, error=error, status_code=422)


@router.post("/members/{user_id}/role")
def update_member_role_form(
    request: Request,
    user_id: int,
    csrf_token: str = Form(...),
    role: str = Form(...),
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    target = db.execute(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == membership.workspace_id,
            WorkspaceMembership.user_id == user_id,
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Member not found")
    try:
        update_member_role(db, target, WorkspaceRole(role), actor=membership.user)
    except (ValueError, LastAdminError) as exc:
        return _members_error(request, db, membership, str(exc))
    return RedirectResponse(
        url=f"/settings/members?workspace_id={membership.workspace_id}", status_code=303
    )


@router.post("/members/{user_id}/remove")
def remove_member_form(
    request: Request,
    user_id: int,
    csrf_token: str = Form(...),
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    target = db.execute(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == membership.workspace_id,
            WorkspaceMembership.user_id == user_id,
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Member not found")
    try:
        remove_member(db, target, actor=membership.user)
    except LastAdminError as exc:
        return _members_error(request, db, membership, str(exc))
    return RedirectResponse(
        url=f"/settings/members?workspace_id={membership.workspace_id}", status_code=303
    )


@router.get("/ai")
def local_ai_page(
    request: Request,
    notice: str | None = None,
    error: str | None = None,
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request,
        "settings/ai.html",
        {
            "active_nav": "settings",
            "ai_status": embedded_ai_status(request.app.state.settings),
            "notice": notice,
            "error": error,
            "csrf_token": token,
            **build_shell_context(request, db, membership.workspace, membership),
        },
    )
    apply_csrf_cookie(request, response, token)
    return response


@router.post("/ai/install")
def install_local_ai_form(
    request: Request,
    csrf_token: str = Form(...),
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    status = embedded_ai_status(request.app.state.settings)
    if status.installed:
        return local_ai_page(
            request, notice="Threnda Local AI is already installed.", membership=membership, db=db
        )
    if not status.supported:
        return local_ai_page(
            request,
            error="This operating system is not yet supported by the embedded AI bundle.",
            membership=membership,
            db=db,
        )
    started = start_embedded_ai_install(request.app.state.settings)
    notice = (
        "Local AI installation started. The verified model is about 1.2 GB; refresh this "
        "page to see its status."
        if started
        else "Local AI installation is already running."
    )
    return local_ai_page(request, notice=notice, membership=membership, db=db)


@router.post("/ai/test")
def test_local_ai_form(
    request: Request,
    csrf_token: str = Form(...),
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    try:
        result = EmbeddedAIProvider(request.app.state.settings).summarize_finding(
            "Synthetic test finding: a demonstration service exposed a sample marker file."
        )
    except AIUnavailableError as exc:
        return local_ai_page(request, error=str(exc), membership=membership, db=db)
    return local_ai_page(
        request,
        notice=f"Local AI responded successfully using {result.model}.",
        membership=membership,
        db=db,
    )


@router.get("/webhooks")
def webhooks_page(
    request: Request,
    notice: str | None = None,
    error: str | None = None,
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    endpoints = (
        db.execute(
            select(WebhookEndpoint)
            .where(WebhookEndpoint.workspace_id == membership.workspace_id)
            .order_by(WebhookEndpoint.id)
        )
        .scalars()
        .all()
    )
    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request,
        "settings/webhooks.html",
        {
            "active_nav": "settings",
            "endpoints": endpoints,
            "notice": notice,
            "error": error,
            "csrf_token": token,
            **build_shell_context(request, db, membership.workspace, membership),
        },
    )
    apply_csrf_cookie(request, response, token)
    return response


@router.post("/webhooks")
def create_webhook_form(
    request: Request,
    csrf_token: str = Form(...),
    url: str = Form(...),
    description: str = Form(default=""),
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    try:
        _endpoint, secret = create_webhook(
            db,
            membership.workspace,
            url=url.strip(),
            description=description.strip() or None,
            settings=request.app.state.settings,
            actor=membership.user,
        )
    except InvalidWebhookUrlError as exc:
        return webhooks_page(request, error=str(exc), membership=membership, db=db)
    return webhooks_page(
        request,
        notice=f"Webhook created. Signing secret (shown once): {secret}",
        membership=membership,
        db=db,
    )


@router.post("/webhooks/{endpoint_id}/toggle")
def toggle_webhook_form(
    request: Request,
    endpoint_id: int,
    csrf_token: str = Form(...),
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    endpoint = db.get(WebhookEndpoint, endpoint_id)
    if endpoint is None or endpoint.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    set_webhook_enabled(db, endpoint, not endpoint.enabled, actor=membership.user)
    return RedirectResponse(
        url=f"/settings/webhooks?workspace_id={membership.workspace_id}", status_code=303
    )


@router.post("/webhooks/{endpoint_id}/delete")
def delete_webhook_form(
    request: Request,
    endpoint_id: int,
    csrf_token: str = Form(...),
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    endpoint = db.get(WebhookEndpoint, endpoint_id)
    if endpoint is None or endpoint.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    delete_webhook(db, endpoint, actor=membership.user)
    return RedirectResponse(
        url=f"/settings/webhooks?workspace_id={membership.workspace_id}", status_code=303
    )


@router.post("/webhooks/{endpoint_id}/trigger")
def trigger_webhook_form(
    request: Request,
    endpoint_id: int,
    csrf_token: str = Form(...),
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    endpoint = db.get(WebhookEndpoint, endpoint_id)
    if endpoint is None or endpoint.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    if not endpoint.enabled:
        return webhooks_page(
            request, error="Enable the webhook before triggering it.", membership=membership, db=db
        )
    try:
        result = deliver_webhook(
            db, endpoint, settings=request.app.state.settings, actor=membership.user
        )
    except InvalidWebhookUrlError as exc:
        return webhooks_page(request, error=str(exc), membership=membership, db=db)
    if result.ok:
        return webhooks_page(
            request, notice=f"Delivered (HTTP {result.status_code}).", membership=membership, db=db
        )
    return webhooks_page(
        request,
        error=f"Delivery failed: {result.error or f'HTTP {result.status_code}'}",
        membership=membership,
        db=db,
    )


@router.get("/audit-log")
def audit_log_page(
    request: Request,
    offset: int = 0,
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    entries = (
        db.execute(
            select(AuditLogEntry)
            .where(AuditLogEntry.workspace_id == membership.workspace_id)
            .order_by(AuditLogEntry.id.desc())
            .limit(PAGE_SIZE)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "settings/audit_log.html",
        {
            "active_nav": "settings",
            "entries": entries,
            "offset": offset,
            "page_size": PAGE_SIZE,
            **build_shell_context(request, db, membership.workspace, membership),
        },
    )
