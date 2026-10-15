"""The findings queue, its detail panel, and the exposure-update actions.

Filtering/sorting/pagination is implemented directly against the ORM here
rather than by calling ``GET /api/v1/exposures`` internally — arbitrary
column sorting isn't part of that endpoint's contract (it always orders by
risk score) and there's no reason to add an HTTP round trip to a process
already holding the same database session. Every write action goes through
``threnda.app.services.exposures.apply_exposure_update`` — the same audited
logic ``PATCH /api/v1/exposures/{id}`` uses — so the audit trail, and the
"no-op writes aren't audited" rule, are identical between the JSON API and
this UI.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload, undefer
from starlette.responses import HTMLResponse, RedirectResponse, Response

from threnda.app.api.deps import get_db
from threnda.app.models.asset import Asset
from threnda.app.models.enums import (
    AssetCriticality,
    ExposureStatus,
    PossibleDuplicateStatus,
    RetestOutcome,
    SeverityLevel,
    WorkspaceRole,
)
from threnda.app.models.exposure import Exposure
from threnda.app.models.finding import Finding
from threnda.app.models.mixins import utcnow
from threnda.app.models.possible_duplicate import PossibleDuplicate
from threnda.app.models.retest import Retest
from threnda.app.models.workspace import Workspace
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.services.collaboration import (
    CollaborationError,
    add_attachment,
    add_comment,
    visible_attachments,
    visible_comments,
)
from threnda.app.services.exposures import (
    ACTIVE_STATUSES,
    ExposureChanges,
    InvalidStatusTransitionError,
    allowed_next_statuses,
    apply_exposure_update,
)
from threnda.app.services.manual_findings import ManualFindingInput, create_manual_finding
from threnda.app.services.memberships import role_at_least
from threnda.app.services.retests import RetestNotAllowedError, record_retest
from threnda.app.services.risk_scoring import compute_risk_score
from threnda.app.web.csrf import apply_csrf_cookie, get_csrf_token, verify_csrf
from threnda.app.web.deps import (
    build_shell_context,
    get_current_membership,
    require_analyst,
    templates,
)

router = APIRouter(prefix="/exposures", tags=["web-exposures"])

PAGE_SIZE = 50
DetailTab = Literal[
    "details",
    "remediation",
    "discussion",
    "retest",
    "timeline",
    "evidence",
    "identity",
    "risk",
]
#: Tabs shown in the panel, in order (``identity`` stays reachable by URL).
PANEL_TABS: tuple[tuple[str, str], ...] = (
    ("details", "Details"),
    ("remediation", "Workflow"),
    ("discussion", "Discussion"),
    ("retest", "Retest"),
    ("timeline", "Timeline"),
    ("evidence", "Sources"),
    ("risk", "Risk"),
)

_SORT_COLUMNS: dict[str, Any] = {
    "risk": Exposure.risk_score.desc(),
    "age": Exposure.first_seen.asc(),
    "last_seen": Exposure.last_seen.desc(),
}


def _get_exposure_or_404(db: Session, workspace_id: int, exposure_id: int) -> Exposure:
    exposure = db.get(Exposure, exposure_id)
    if exposure is None or exposure.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Finding not found")
    return exposure


def _filtered_query(
    *,
    workspace_id: int,
    status: ExposureStatus | None,
    severity: SeverityLevel | None,
    owner: str | None,
    q: str | None,
    min_risk: int | None = None,
    max_risk: int | None = None,
    overdue: bool = False,
) -> Select[tuple[Exposure]]:
    stmt = select(Exposure).where(Exposure.workspace_id == workspace_id)
    if overdue:
        stmt = stmt.where(
            Exposure.status.in_(ACTIVE_STATUSES),
            Exposure.remediation_target_date.is_not(None),
            Exposure.remediation_target_date < utcnow().date(),
        )
    if status is not None:
        stmt = stmt.where(Exposure.status == status)
    if severity is not None:
        stmt = stmt.where(Exposure.severity == severity)
    if owner:
        stmt = stmt.where(Exposure.owner == owner)
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
    if min_risk is not None:
        stmt = stmt.where(Exposure.risk_score >= min_risk)
    if max_risk is not None:
        stmt = stmt.where(Exposure.risk_score <= max_risk)
    return stmt


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _row_view(exposure: Exposure) -> dict[str, object]:
    """Flatten an exposure's linked findings into simple display fields.

    Computed here rather than in the template so the template doesn't need
    to walk ``exposure.findings``/``.asset``/``.source`` relationships
    itself.
    """
    findings = exposure.findings
    asset_name = exposure.affected_system or next(
        (f.asset.name for f in findings if f.asset is not None), None
    )
    source_names = sorted({f.source.name for f in findings if f.source is not None})
    if not source_names:
        source_label = "—"
    elif len(source_names) == 1:
        source_label = source_names[0]
    else:
        source_label = f"{len(source_names)} sources"

    return {
        "exposure": exposure,
        "asset": asset_name or "—",
        "source": source_label,
        "dup_count": len(findings),
    }


@router.get("")
def queue(
    request: Request,
    status: ExposureStatus | None = None,
    severity: SeverityLevel | None = None,
    owner: str | None = None,
    q: str | None = None,
    min_risk: int | None = None,
    max_risk: int | None = None,
    overdue: bool = False,
    sort: str = "risk",
    offset: int = 0,
    open: int | None = None,
    membership: WorkspaceMembership = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> Response:
    workspace = membership.workspace
    filtered = _filtered_query(
        workspace_id=workspace.id,
        status=status,
        severity=severity,
        owner=owner,
        q=q,
        min_risk=min_risk,
        max_risk=max_risk,
        overdue=overdue,
    )
    total = db.execute(select(func.count()).select_from(filtered.subquery())).scalar_one()

    order = _SORT_COLUMNS.get(sort, _SORT_COLUMNS["risk"])
    stmt = (
        filtered.options(
            undefer(Exposure.affected_system),
            selectinload(Exposure.findings).selectinload(Finding.asset),
            selectinload(Exposure.findings).selectinload(Finding.source),
        )
        .order_by(order, Exposure.id)
        .limit(PAGE_SIZE)
        .offset(offset)
    )
    exposures = db.execute(stmt).scalars().all()

    ever_any = db.execute(
        select(func.count()).select_from(Exposure).where(Exposure.workspace_id == workspace.id)
    ).scalar_one()

    rows = [_row_view(exposure) for exposure in exposures]
    token = get_csrf_token(request)

    context = {
        "active_nav": "exposures",
        "rows": rows,
        "total": total,
        "ever_any": ever_any,
        "offset": offset,
        "page_size": PAGE_SIZE,
        "status": status,
        "severity": severity,
        "owner": owner,
        "q": q,
        "min_risk": min_risk,
        "max_risk": max_risk,
        "overdue": overdue,
        "sort": sort,
        "statuses": list(ExposureStatus),
        "severities": list(SeverityLevel),
        "csrf_token": token,
        **build_shell_context(request, db, workspace, membership),
    }

    if open is not None and not _is_htmx(request):
        opened = db.get(Exposure, open)
        if opened is not None and opened.workspace_id == workspace.id:
            # The panel's own keys (exposure, tab, ...) don't collide with
            # the queue's except for identical shared ones (workspace,
            # csrf_token, statuses, severities).
            context.update(
                _panel_context(request, db, workspace, opened, tab="details", membership=membership)
            )
            context["panel_open"] = True

    template = "exposures/_queue_inner.html" if _is_htmx(request) else "exposures/queue.html"
    response = templates.TemplateResponse(request, template, context)
    apply_csrf_cookie(request, response, token)
    return response


@router.get("/panel/close")
def close_panel(
    _membership: WorkspaceMembership = Depends(get_current_membership),
) -> HTMLResponse:
    return HTMLResponse('<div class="panel-placeholder">Select a finding to see its detail.</div>')


def _linked_assets(exposure: Exposure) -> list[Asset]:
    """Distinct assets linked through this exposure's findings.

    Used by the Risk tab's per-asset edit forms — an exposure usually
    correlates findings on one asset, but the model allows more.
    """
    seen: dict[int, Asset] = {}
    for finding in exposure.findings:
        if finding.asset is not None:
            seen[finding.asset.id] = finding.asset
    return list(seen.values())


def _panel_context(
    request: Request,
    db: Session,
    workspace: Workspace,
    exposure: Exposure,
    *,
    tab: DetailTab,
    membership: WorkspaceMembership,
    error: str | None = None,
    notice: str | None = None,
) -> dict[str, object]:
    pending_duplicate = db.execute(
        select(PossibleDuplicate)
        .where(
            PossibleDuplicate.workspace_id == workspace.id,
            PossibleDuplicate.status == PossibleDuplicateStatus.PENDING,
            PossibleDuplicate.candidate_exposure_id == exposure.id,
        )
        .limit(1)
    ).scalar_one_or_none()

    return {
        "exposure": exposure,
        "findings": list(exposure.findings),
        "history": list(exposure.history),
        "pending_duplicate": pending_duplicate,
        "tab": tab,
        "csrf_token": get_csrf_token(request),
        "workspace": workspace,
        "statuses": list(ExposureStatus),
        "allowed_statuses": sorted(allowed_next_statuses(exposure.status), key=lambda s: s.value),
        "risk_result": compute_risk_score(exposure),
        "linked_assets": _linked_assets(exposure),
        "criticalities": list(AssetCriticality),
        "severities": list(SeverityLevel),
        "error": error,
        "notice": notice,
        "panel_tabs": PANEL_TABS,
        "comments": visible_comments(db, exposure, membership.role),
        "attachments": visible_attachments(db, exposure, membership.role),
        "retests": db.execute(
            select(Retest)
            .where(Retest.workspace_id == workspace.id, Retest.exposure_id == exposure.id)
            .order_by(Retest.performed_at.desc())
        )
        .scalars()
        .all(),
        "can_write": role_at_least(membership.role, WorkspaceRole.ANALYST),
        "max_attachment_bytes": request.app.state.settings.attachment_max_bytes,
        "today": utcnow().date(),
    }


def _render_detail_panel(
    request: Request,
    db: Session,
    workspace: Workspace,
    exposure: Exposure,
    *,
    tab: DetailTab,
    membership: WorkspaceMembership,
    error: str | None = None,
    notice: str | None = None,
    sync_row: bool = False,
) -> Response:
    """Render the detail panel; optionally an out-of-band refresh of its
    queue row too (``sync_row``) — used after a write that could change
    what the row displays (status pill, risk score), so the panel and the
    queue behind it never show inconsistent state without a full reload.
    """
    context = _panel_context(
        request,
        db,
        workspace,
        exposure,
        tab=tab,
        membership=membership,
        error=error,
        notice=notice,
    )
    template = "exposures/_detail_panel.html"
    if sync_row:
        context["row"] = _row_view(exposure)
        context["oob"] = True
        template = "exposures/_panel_and_row_oob.html"

    token = get_csrf_token(request)
    response = templates.TemplateResponse(request, template, context)
    apply_csrf_cookie(request, response, token)
    return response


@router.get("/{exposure_id}/panel")
def detail_panel(
    request: Request,
    exposure_id: int,
    tab: DetailTab = "details",
    membership: WorkspaceMembership = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> Response:
    workspace = membership.workspace
    exposure = _get_exposure_or_404(db, workspace.id, exposure_id)
    return _render_detail_panel(request, db, workspace, exposure, tab=tab, membership=membership)


@router.get("/new")
def new_finding_form(
    request: Request,
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    return _render_new_finding(request, db, membership, values={}, error=None)


def _render_new_finding(
    request: Request,
    db: Session,
    membership: WorkspaceMembership,
    *,
    values: dict[str, str],
    error: str | None,
    status_code: int = 200,
) -> Response:
    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request,
        "exposures/new.html",
        {
            "active_nav": "exposures",
            "values": values,
            "error": error,
            "severities": list(SeverityLevel),
            "csrf_token": token,
            **build_shell_context(request, db, membership.workspace, membership),
        },
        status_code=status_code,
    )
    apply_csrf_cookie(request, response, token)
    return response


@router.post("/new")
def create_finding(
    request: Request,
    csrf_token: str = Form(...),
    title: str = Form(default=""),
    severity: str = Form(default=""),
    affected_system: str = Form(default=""),
    description: str = Form(default=""),
    remediation: str = Form(default=""),
    owner: str = Form(default=""),
    due_date: str = Form(default=""),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    # Registered before POST /{exposure_id} for the same reason as /bulk.
    verify_csrf(request, csrf_token)
    values = {
        "title": title,
        "severity": severity,
        "affected_system": affected_system,
        "description": description,
        "remediation": remediation,
        "owner": owner,
        "due_date": due_date,
    }
    try:
        resolved_severity = SeverityLevel(severity)
    except ValueError:
        return _render_new_finding(
            request, db, membership, values=values, error="Choose a severity.", status_code=422
        )
    try:
        due = date.fromisoformat(due_date) if due_date else None
    except ValueError:
        return _render_new_finding(
            request,
            db,
            membership,
            values=values,
            error="Due date must be a valid date (YYYY-MM-DD).",
            status_code=422,
        )
    try:
        exposure = create_manual_finding(
            db,
            membership.workspace,
            ManualFindingInput(
                title=title,
                severity=resolved_severity,
                description=description,
                affected_system=affected_system,
                remediation=remediation,
                owner=owner,
                due_date=due,
            ),
            actor=membership.user.username,
        )
    except ValueError as exc:
        return _render_new_finding(
            request, db, membership, values=values, error=str(exc), status_code=422
        )
    return RedirectResponse(
        url=f"/exposures?workspace_id={membership.workspace_id}&open={exposure.id}",
        status_code=303,
    )


@router.post("/bulk")
def bulk_update(
    request: Request,
    csrf_token: str = Form(...),
    action: str = Form(...),
    return_qs: str = Form(default=""),
    ids: list[int] = Form(default=[]),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    # Registered before POST /{exposure_id} — Starlette matches path routes
    # in registration order, and `{exposure_id}` (an unconverted string
    # segment at this stage) would otherwise greedily match "bulk" too,
    # only failing (422) once FastAPI tries to coerce "bulk" into an int.
    verify_csrf(request, csrf_token)
    workspace = membership.workspace

    changes: ExposureChanges
    if action == "acknowledge":
        changes = {"acknowledged": True}
    elif action == "in_progress":
        changes = {"status": ExposureStatus.IN_PROGRESS}
    elif action == "accept_risk":
        changes = {"status": ExposureStatus.RISK_ACCEPTED}
    else:
        raise HTTPException(status_code=422, detail=f"Unknown bulk action {action!r}")

    for exposure_id in ids:
        exposure = db.get(Exposure, exposure_id)
        if exposure is None or exposure.workspace_id != workspace.id:
            continue
        try:
            apply_exposure_update(db, exposure, changes, note=None, actor=membership.user.username)
        except InvalidStatusTransitionError:
            # A bulk action names one target status for every selected
            # exposure; one of them not being able to make that specific
            # move (e.g. already RESOLVED) skips just that one rather than
            # failing the whole batch — same "skip what doesn't apply"
            # policy as the line above for a foreign-workspace id.
            continue

    query = (
        f"{return_qs}&workspace_id={workspace.id}" if return_qs else f"workspace_id={workspace.id}"
    )
    return RedirectResponse(url=f"/exposures?{query}", status_code=303)


@router.post("/{exposure_id}")
def update_exposure(
    request: Request,
    exposure_id: int,
    csrf_token: str = Form(...),
    status: str | None = Form(default=None),
    owner: str | None = Form(default=None),
    acknowledged: str | None = Form(default=None),
    remediation_target_date: str | None = Form(default=None),
    remediation: str | None = Form(default=None),
    note: str | None = Form(default=None),
    tab: DetailTab = Form(default="remediation"),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    workspace = membership.workspace
    exposure = _get_exposure_or_404(db, workspace.id, exposure_id)

    changes: ExposureChanges = {}
    try:
        if status is not None:
            changes["status"] = ExposureStatus(status)
        if remediation_target_date is not None:
            changes["remediation_target_date"] = (
                date.fromisoformat(remediation_target_date) if remediation_target_date else None
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if owner is not None:
        changes["owner"] = owner.strip() or None
    if acknowledged is not None:
        changes["acknowledged"] = acknowledged == "true"
    if remediation is not None:
        changes["remediation"] = remediation.strip() or None

    try:
        apply_exposure_update(
            db,
            exposure,
            changes,
            note=(note.strip() if note else None),
            actor=membership.user.username,
        )
    except InvalidStatusTransitionError as exc:
        return _render_detail_panel(
            request, db, workspace, exposure, tab=tab, membership=membership, error=str(exc)
        )

    return _render_detail_panel(
        request, db, workspace, exposure, tab=tab, membership=membership, sync_row=True
    )


@router.post("/{exposure_id}/details")
def update_details(
    request: Request,
    exposure_id: int,
    csrf_token: str = Form(...),
    title: str = Form(...),
    severity: str = Form(...),
    affected_system: str = Form(default=""),
    description: str = Form(default=""),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    workspace = membership.workspace
    exposure = _get_exposure_or_404(db, workspace.id, exposure_id)
    try:
        changes: ExposureChanges = {
            "title": title.strip(),
            "severity": SeverityLevel(severity),
            "affected_system": affected_system.strip() or None,
            "description": description.strip() or None,
        }
        apply_exposure_update(db, exposure, changes, note=None, actor=membership.user.username)
    except ValueError as exc:
        return _render_detail_panel(
            request, db, workspace, exposure, tab="details", membership=membership, error=str(exc)
        )
    return _render_detail_panel(
        request,
        db,
        workspace,
        exposure,
        tab="details",
        membership=membership,
        notice="Saved.",
        sync_row=True,
    )


@router.post("/{exposure_id}/comments")
def post_comment(
    request: Request,
    exposure_id: int,
    csrf_token: str = Form(...),
    body: str = Form(default=""),
    internal: str | None = Form(default=None),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    workspace = membership.workspace
    exposure = _get_exposure_or_404(db, workspace.id, exposure_id)
    try:
        add_comment(
            db,
            exposure,
            body=body,
            internal=internal == "true",
            actor=membership.user,
            actor_role=membership.role,
        )
    except CollaborationError as exc:
        return _render_detail_panel(
            request,
            db,
            workspace,
            exposure,
            tab="discussion",
            membership=membership,
            error=str(exc),
        )
    return _render_detail_panel(
        request, db, workspace, exposure, tab="discussion", membership=membership
    )


@router.post("/{exposure_id}/attachments")
async def post_attachment(
    request: Request,
    exposure_id: int,
    csrf_token: str = Form(...),
    file: UploadFile = File(...),
    internal: str | None = Form(default=None),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    workspace = membership.workspace
    exposure = _get_exposure_or_404(db, workspace.id, exposure_id)
    max_bytes: int = request.app.state.settings.attachment_max_bytes
    raw = await file.read(max_bytes + 1)
    try:
        add_attachment(
            db,
            exposure,
            filename=file.filename,
            raw=raw,
            internal=internal == "true",
            actor=membership.user,
            actor_role=membership.role,
            max_bytes=max_bytes,
        )
    except CollaborationError as exc:
        return _render_detail_panel(
            request,
            db,
            workspace,
            exposure,
            tab="discussion",
            membership=membership,
            error=str(exc),
        )
    return _render_detail_panel(
        request,
        db,
        workspace,
        exposure,
        tab="discussion",
        membership=membership,
        notice="Evidence uploaded.",
    )


@router.post("/{exposure_id}/retest")
def post_retest(
    request: Request,
    exposure_id: int,
    csrf_token: str = Form(...),
    outcome: str = Form(...),
    notes: str = Form(default=""),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    workspace = membership.workspace
    exposure = _get_exposure_or_404(db, workspace.id, exposure_id)
    try:
        resolved_outcome = RetestOutcome(outcome)
        record_retest(
            db,
            exposure,
            outcome=resolved_outcome,
            notes=notes,
            actor=membership.user,
            actor_role=membership.role,
        )
    except (ValueError, RetestNotAllowedError) as exc:
        return _render_detail_panel(
            request, db, workspace, exposure, tab="retest", membership=membership, error=str(exc)
        )
    notice = (
        "Retest passed — the finding is now verified closed."
        if resolved_outcome == RetestOutcome.PASSED
        else "Retest failed — the finding is reopened."
    )
    return _render_detail_panel(
        request,
        db,
        workspace,
        exposure,
        tab="retest",
        membership=membership,
        notice=notice,
        sync_row=True,
    )
