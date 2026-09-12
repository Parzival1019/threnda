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

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload
from starlette.responses import HTMLResponse, RedirectResponse, Response

from threnda.app.api.deps import get_db
from threnda.app.models.asset import Asset
from threnda.app.models.enums import (
    AssetCriticality,
    ExposureStatus,
    PossibleDuplicateStatus,
    SeverityLevel,
)
from threnda.app.models.exposure import Exposure
from threnda.app.models.finding import Finding
from threnda.app.models.possible_duplicate import PossibleDuplicate
from threnda.app.models.workspace import Workspace
from threnda.app.services.exposures import (
    ExposureChanges,
    InvalidStatusTransitionError,
    allowed_next_statuses,
    apply_exposure_update,
)
from threnda.app.services.risk_scoring import compute_risk_score
from threnda.app.web.csrf import apply_csrf_cookie, get_csrf_token, verify_csrf
from threnda.app.web.deps import build_shell_context, get_current_workspace, templates

router = APIRouter(prefix="/exposures", tags=["web-exposures"])

# Web-UI-only placeholder identity, distinct from the API's own "system"
# default — same Milestone-7-pending caveat as every `actor` field in this
# codebase: there is no authentication yet, so this cannot name a real user.
ACTOR = "web"

PAGE_SIZE = 50
DetailTab = Literal["evidence", "identity", "timeline", "remediation", "risk"]

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
) -> Select[tuple[Exposure]]:
    stmt = select(Exposure).where(Exposure.workspace_id == workspace_id)
    if status is not None:
        stmt = stmt.where(Exposure.status == status)
    if severity is not None:
        stmt = stmt.where(Exposure.severity == severity)
    if owner:
        stmt = stmt.where(Exposure.owner == owner)
    if q:
        stmt = stmt.where(Exposure.title.ilike(f"%{q}%"))
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
    asset_name = next((f.asset.name for f in findings if f.asset is not None), None)
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
    sort: str = "risk",
    offset: int = 0,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> Response:
    filtered = _filtered_query(
        workspace_id=workspace.id,
        status=status,
        severity=severity,
        owner=owner,
        q=q,
        min_risk=min_risk,
        max_risk=max_risk,
    )
    total = db.execute(select(func.count()).select_from(filtered.subquery())).scalar_one()

    order = _SORT_COLUMNS.get(sort, _SORT_COLUMNS["risk"])
    stmt = (
        filtered.options(
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
        "sort": sort,
        "statuses": list(ExposureStatus),
        "severities": list(SeverityLevel),
        "csrf_token": token,
        **build_shell_context(db, workspace),
    }

    template = "exposures/_queue_inner.html" if _is_htmx(request) else "exposures/queue.html"
    response = templates.TemplateResponse(request, template, context)
    apply_csrf_cookie(request, response, token)
    return response


@router.get("/panel/close")
def close_panel(workspace: Workspace = Depends(get_current_workspace)) -> HTMLResponse:
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
    error: str | None = None,
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
        "error": error,
    }


def _render_detail_panel(
    request: Request,
    db: Session,
    workspace: Workspace,
    exposure: Exposure,
    *,
    tab: DetailTab,
    error: str | None = None,
    sync_row: bool = False,
) -> Response:
    """Render the detail panel; optionally an out-of-band refresh of its
    queue row too (``sync_row``) — used after a write that could change
    what the row displays (status pill, risk score), so the panel and the
    queue behind it never show inconsistent state without a full reload.
    """
    context = _panel_context(request, db, workspace, exposure, tab=tab, error=error)
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
    tab: DetailTab = "evidence",
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> Response:
    exposure = _get_exposure_or_404(db, workspace.id, exposure_id)
    return _render_detail_panel(request, db, workspace, exposure, tab=tab)


@router.post("/bulk")
def bulk_update(
    request: Request,
    csrf_token: str = Form(...),
    action: str = Form(...),
    return_qs: str = Form(default=""),
    ids: list[int] = Form(default=[]),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> Response:
    # Registered before POST /{exposure_id} — Starlette matches path routes
    # in registration order, and `{exposure_id}` (an unconverted string
    # segment at this stage) would otherwise greedily match "bulk" too,
    # only failing (422) once FastAPI tries to coerce "bulk" into an int.
    verify_csrf(request, csrf_token)

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
            apply_exposure_update(db, exposure, changes, note=None, actor=ACTOR)
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
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
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
            db, exposure, changes, note=(note.strip() if note else None), actor=ACTOR
        )
    except InvalidStatusTransitionError as exc:
        return _render_detail_panel(request, db, workspace, exposure, tab=tab, error=str(exc))

    return _render_detail_panel(request, db, workspace, exposure, tab=tab, sync_row=True)
