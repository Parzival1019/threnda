"""Read and update access to exposures — the user-facing finding queue.

``PATCH /{exposure_id}`` applies partial updates and writes an
``ExposureHistory`` audit row for every meaningful change. Since the
pilot-readiness milestone this router also carries manual finding entry,
retests (the only path to *verified closed*), comments, evidence
attachments and the client-side status actions.

Access levels (docs/security.md):

- ``client`` — list/read findings of their own project, read the
  client-visible history/comments/attachments, comment, upload evidence and
  mark a finding in progress / fixed (``client-status``);
- ``viewer`` — everything a client can read, plus internal content;
- ``analyst`` (pentester) — create/edit findings, retest, internal content;
- ``admin`` — everything.
"""

from __future__ import annotations

from typing import cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from threnda.app.api.deps import get_db, require_analyst, require_client
from threnda.app.models.enums import AuditEventType, ExposureStatus, SeverityLevel
from threnda.app.models.exposure import Exposure
from threnda.app.models.mixins import utcnow
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.schemas.common import Page
from threnda.app.schemas.exposure import (
    AttachmentRead,
    ClientStatusUpdate,
    CommentCreate,
    CommentRead,
    ExposureCreate,
    ExposureDetailRead,
    ExposureHistoryRead,
    ExposureRead,
    ExposureUpdate,
    RetestCreate,
    RetestRead,
    RiskScoreComponentRead,
)
from threnda.app.services.audit import record_audit_event
from threnda.app.services.collaboration import (
    CollaborationError,
    add_attachment,
    add_comment,
    client_set_status,
    get_visible_attachment,
    visible_attachments,
    visible_comments,
    visible_history,
)
from threnda.app.services.exposures import (
    ACTIVE_STATUSES,
    ExposureChanges,
    InvalidStatusTransitionError,
    apply_exposure_update,
)
from threnda.app.services.manual_findings import ManualFindingInput, create_manual_finding
from threnda.app.services.retests import RetestNotAllowedError, record_retest
from threnda.app.services.risk_scoring import compute_risk_score

router = APIRouter(prefix="/exposures", tags=["exposures"])
attachments_router = APIRouter(prefix="/attachments", tags=["exposures"])


def _get_exposure_or_404(db: Session, workspace_id: int, exposure_id: int) -> Exposure:
    """Fetch an exposure scoped to a workspace.

    Returns 404 (not 403) when the exposure exists but belongs to a
    different workspace, so a caller can't probe ids across projects.
    """
    exposure = db.get(Exposure, exposure_id)
    if exposure is None or exposure.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Exposure not found")
    return exposure


@router.get("", response_model=Page[ExposureRead])
def list_exposures(
    status: ExposureStatus | None = None,
    severity: SeverityLevel | None = None,
    owner: str | None = None,
    q: str | None = Query(
        default=None,
        description="Case-insensitive search over title, description, affected system and owner.",
    ),
    overdue: bool = Query(default=False, description="Only active findings past their due date."),
    min_risk_score: int | None = Query(default=None, ge=0, le=100),
    max_risk_score: int | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    membership: WorkspaceMembership = Depends(require_client),
    db: Session = Depends(get_db),
) -> Page[ExposureRead]:
    stmt = select(Exposure).where(Exposure.workspace_id == membership.workspace_id)
    if status is not None:
        stmt = stmt.where(Exposure.status == status)
    if severity is not None:
        stmt = stmt.where(Exposure.severity == severity)
    if owner is not None:
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
    if overdue:
        stmt = stmt.where(
            Exposure.status.in_(ACTIVE_STATUSES),
            Exposure.remediation_target_date.is_not(None),
            Exposure.remediation_target_date < utcnow().date(),
        )
    if min_risk_score is not None:
        stmt = stmt.where(Exposure.risk_score >= min_risk_score)
    if max_risk_score is not None:
        stmt = stmt.where(Exposure.risk_score <= max_risk_score)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    stmt = stmt.order_by(Exposure.risk_score.desc(), Exposure.id).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return Page[ExposureRead](
        items=[ExposureRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ExposureRead, status_code=201)
def create_exposure(
    payload: ExposureCreate,
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> ExposureRead:
    """Manual finding entry."""
    try:
        exposure = create_manual_finding(
            db,
            membership.workspace,
            ManualFindingInput(
                title=payload.title,
                severity=payload.severity,
                description=payload.description,
                affected_system=payload.affected_system,
                remediation=payload.remediation,
                owner=payload.owner,
                due_date=payload.remediation_target_date,
            ),
            actor=membership.user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ExposureRead.model_validate(exposure)


@router.get("/{exposure_id}", response_model=ExposureDetailRead)
def get_exposure(
    exposure_id: int,
    membership: WorkspaceMembership = Depends(require_client),
    db: Session = Depends(get_db),
) -> ExposureDetailRead:
    """Fetch one exposure, including a live risk-score breakdown.

    ``risk_score``/``risk_score_breakdown`` here are computed fresh on
    every call rather than read from the persisted column — see
    ``threnda.app.services.risk_scoring``'s module docstring.
    """
    exposure = _get_exposure_or_404(db, membership.workspace_id, exposure_id)
    result = compute_risk_score(exposure)
    base = ExposureRead.model_validate(exposure).model_dump()
    base["risk_score"] = result.total
    return ExposureDetailRead(
        **base,
        risk_score_breakdown=[
            RiskScoreComponentRead(label=c.label, points=c.points, detail=c.detail)
            for c in result.components
        ],
    )


@router.patch("/{exposure_id}", response_model=ExposureRead)
def update_exposure(
    exposure_id: int,
    payload: ExposureUpdate,
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> ExposureRead:
    exposure = _get_exposure_or_404(db, membership.workspace_id, exposure_id)
    changes = cast(ExposureChanges, payload.model_dump(exclude_unset=True, exclude={"note"}))
    try:
        apply_exposure_update(
            db, exposure, changes, note=payload.note, actor=membership.user.username
        )
    except InvalidStatusTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ExposureRead.model_validate(exposure)


@router.get("/{exposure_id}/history", response_model=list[ExposureHistoryRead])
def get_history(
    exposure_id: int,
    membership: WorkspaceMembership = Depends(require_client),
    db: Session = Depends(get_db),
) -> list[ExposureHistoryRead]:
    exposure = _get_exposure_or_404(db, membership.workspace_id, exposure_id)
    return [
        ExposureHistoryRead.model_validate(e) for e in visible_history(exposure, membership.role)
    ]


@router.post("/{exposure_id}/retest", response_model=RetestRead, status_code=201)
def create_retest(
    exposure_id: int,
    payload: RetestCreate,
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> RetestRead:
    """Record a retest: ``passed`` closes the finding as verified, ``failed`` reopens it."""
    exposure = _get_exposure_or_404(db, membership.workspace_id, exposure_id)
    try:
        retest = record_retest(
            db,
            exposure,
            outcome=payload.outcome,
            notes=payload.notes,
            actor=membership.user,
            actor_role=membership.role,
        )
    except RetestNotAllowedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RetestRead.model_validate(retest)


@router.post("/{exposure_id}/client-status", response_model=ExposureRead)
def client_status(
    exposure_id: int,
    payload: ClientStatusUpdate,
    membership: WorkspaceMembership = Depends(require_client),
    db: Session = Depends(get_db),
) -> ExposureRead:
    """Mark a finding "in progress" or "fixed — ready for retest".

    Available to every project member, including client users; it can never
    close a finding (see ``threnda.app.services.collaboration``).
    """
    exposure = _get_exposure_or_404(db, membership.workspace_id, exposure_id)
    try:
        client_set_status(
            db, exposure, status=payload.status, note=payload.note, actor=membership.user
        )
    except InvalidStatusTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CollaborationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ExposureRead.model_validate(exposure)


@router.get("/{exposure_id}/comments", response_model=list[CommentRead])
def list_comments(
    exposure_id: int,
    membership: WorkspaceMembership = Depends(require_client),
    db: Session = Depends(get_db),
) -> list[CommentRead]:
    exposure = _get_exposure_or_404(db, membership.workspace_id, exposure_id)
    return [CommentRead.model_validate(c) for c in visible_comments(db, exposure, membership.role)]


@router.post("/{exposure_id}/comments", response_model=CommentRead, status_code=201)
def create_comment(
    exposure_id: int,
    payload: CommentCreate,
    membership: WorkspaceMembership = Depends(require_client),
    db: Session = Depends(get_db),
) -> CommentRead:
    """``internal: true`` is ignored for client users (their comments are always visible)."""
    exposure = _get_exposure_or_404(db, membership.workspace_id, exposure_id)
    try:
        comment = add_comment(
            db,
            exposure,
            body=payload.body,
            internal=payload.internal,
            actor=membership.user,
            actor_role=membership.role,
        )
    except CollaborationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CommentRead.model_validate(comment)


@router.get("/{exposure_id}/attachments", response_model=list[AttachmentRead])
def list_attachments(
    exposure_id: int,
    membership: WorkspaceMembership = Depends(require_client),
    db: Session = Depends(get_db),
) -> list[AttachmentRead]:
    exposure = _get_exposure_or_404(db, membership.workspace_id, exposure_id)
    return [
        AttachmentRead.model_validate(a) for a in visible_attachments(db, exposure, membership.role)
    ]


@router.post("/{exposure_id}/attachments", response_model=AttachmentRead, status_code=201)
async def upload_attachment(
    request: Request,
    exposure_id: int,
    file: UploadFile = File(...),
    internal: bool = Form(default=False),
    membership: WorkspaceMembership = Depends(require_client),
    db: Session = Depends(get_db),
) -> AttachmentRead:
    exposure = _get_exposure_or_404(db, membership.workspace_id, exposure_id)
    max_bytes: int = request.app.state.settings.attachment_max_bytes
    raw = await file.read(max_bytes + 1)
    try:
        attachment = add_attachment(
            db,
            exposure,
            filename=file.filename,
            raw=raw,
            internal=internal,
            actor=membership.user,
            actor_role=membership.role,
            max_bytes=max_bytes,
        )
    except CollaborationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AttachmentRead.model_validate(attachment)


def attachment_download_response(filename: str, content_type: str, content: bytes) -> Response:
    """Always a download, never rendered inline by the browser.

    ``nosniff`` is already a global header (SecurityHeadersMiddleware); the
    ``sandbox`` CSP is belt-and-braces in case a browser ignores
    ``Content-Disposition``.
    """
    ascii_name = filename.encode("ascii", "ignore").decode() or "attachment"
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "content-disposition": (
                f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
            ),
            "cache-control": "no-store",
            "content-security-policy": "sandbox; default-src 'none'",
        },
    )


@attachments_router.get("/{attachment_id}/download")
def download_attachment(
    request: Request,
    attachment_id: int,
    membership: WorkspaceMembership = Depends(require_client),
    db: Session = Depends(get_db),
) -> Response:
    attachment = get_visible_attachment(db, membership.workspace_id, attachment_id, membership.role)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    record_audit_event(
        db,
        event_type=AuditEventType.ATTACHMENT_DOWNLOADED,
        actor=membership.user,
        workspace=membership.workspace,
        target_type="attachment",
        target_id=attachment.id,
        detail={"filename": attachment.filename, "exposure_id": attachment.exposure_id},
        ip_address=request.client.host if request.client else None,
    )
    return attachment_download_response(
        attachment.filename, attachment.content_type, attachment.content
    )
