"""Server-rendered local document extraction and candidate review."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.responses import RedirectResponse, Response

from threnda.app.api.deps import get_db
from threnda.app.models.document import Document
from threnda.app.models.enums import SeverityLevel, SourceType
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.services.documents.processing import UnsupportedDocumentError, process_document
from threnda.app.services.documents.review import (
    CandidateAlreadyResolvedError,
    CandidateNotFoundError,
    accept_candidate,
    reject_candidate,
)
from threnda.app.web.csrf import apply_csrf_cookie, get_csrf_token, verify_csrf
from threnda.app.web.deps import (
    build_shell_context,
    get_current_membership,
    require_analyst,
    templates,
)

router = APIRouter(prefix="/documents", tags=["web-documents"])


def _page(
    request: Request,
    db: Session,
    workspace: Workspace,
    membership: WorkspaceMembership,
    *,
    notice: str | None = None,
    error: str | None = None,
) -> Response:
    sources = (
        db.execute(
            select(Source)
            .where(
                Source.workspace_id == workspace.id,
                Source.source_type == SourceType.PENTEST_REPORT,
            )
            .order_by(Source.name)
        )
        .scalars()
        .all()
    )
    documents = (
        db.execute(
            select(Document)
            .where(Document.workspace_id == workspace.id)
            .options(selectinload(Document.candidates))
            .order_by(Document.created_at.desc())
        )
        .scalars()
        .all()
    )
    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request,
        "documents/list.html",
        {
            "active_nav": "documents",
            "sources": sources,
            "documents": documents,
            "severities": list(SeverityLevel),
            "csrf_token": token,
            "notice": notice,
            "error": error,
            **build_shell_context(request, db, workspace, membership),
        },
    )
    apply_csrf_cookie(request, response, token)
    return response


@router.get("")
def list_documents(
    request: Request,
    membership: WorkspaceMembership = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> Response:
    return _page(request, db, membership.workspace, membership)


@router.post("")
async def upload_document(
    request: Request,
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    workspace = membership.workspace
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token") or ""))
    upload = form.get("file")
    source_id_raw = form.get("source_id")
    if not isinstance(upload, StarletteUploadFile) or not source_id_raw:
        raise HTTPException(status_code=422, detail="A source and document are required")
    try:
        source_id = int(str(source_id_raw))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid source") from exc
    source = db.get(Source, source_id)
    if source is None or source.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        document = process_document(
            db,
            workspace_id=workspace.id,
            source=source,
            filename=upload.filename,
            media_type=upload.content_type,
            raw=await upload.read(),
            ai_settings=request.app.state.settings,
            use_ai=form.get("use_ai") == "on",
        )
    except (UnsupportedDocumentError, ValueError) as exc:
        return _page(request, db, workspace, membership, error=str(exc))
    return _page(
        request,
        db,
        workspace,
        membership,
        notice=(
            f"{document.filename}: {len(document.candidates)} review candidate(s) ready."
            if document.status.value == "processed"
            else None
        ),
        error=document.error_message if document.status.value == "failed" else None,
    )


@router.post("/candidates/{candidate_id}/accept")
def accept(
    request: Request,
    candidate_id: int,
    csrf_token: str = Form(...),
    title: str = Form(...),
    severity: SeverityLevel = Form(...),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    workspace = membership.workspace
    try:
        accept_candidate(
            db,
            workspace_id=workspace.id,
            candidate_id=candidate_id,
            actor=membership.user.username,
            title=title,
            severity=severity,
        )
    except CandidateNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Candidate not found") from exc
    except CandidateAlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(f"/documents?workspace_id={workspace.id}", status_code=303)


@router.post("/candidates/{candidate_id}/reject")
def reject(
    request: Request,
    candidate_id: int,
    csrf_token: str = Form(...),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response:
    verify_csrf(request, csrf_token)
    workspace = membership.workspace
    try:
        reject_candidate(
            db, workspace_id=workspace.id, candidate_id=candidate_id, actor=membership.user.username
        )
    except CandidateNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Candidate not found") from exc
    except CandidateAlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(f"/documents?workspace_id={workspace.id}", status_code=303)
