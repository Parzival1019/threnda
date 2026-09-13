"""Local document extraction and explicit candidate review API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from threnda.app.api.deps import get_db, require_analyst, require_viewer
from threnda.app.models.candidate_finding import CandidateFinding
from threnda.app.models.document import Document
from threnda.app.models.enums import CandidateFindingStatus
from threnda.app.models.source import Source
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.schemas.document import (
    CandidateDecision,
    CandidateRead,
    DocumentDetailRead,
    DocumentRead,
)
from threnda.app.services.documents.processing import UnsupportedDocumentError, process_document
from threnda.app.services.documents.review import (
    CandidateAlreadyResolvedError,
    CandidateNotFoundError,
    accept_candidate,
    reject_candidate,
)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=list[DocumentRead])
def list_documents(
    membership: WorkspaceMembership = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> list[DocumentRead]:
    rows = db.execute(
        select(Document)
        .where(Document.workspace_id == membership.workspace_id)
        .order_by(Document.created_at.desc())
    ).scalars()
    return [DocumentRead.model_validate(row) for row in rows]


def _get_document_or_404(db: Session, workspace_id: int, document_id: int) -> DocumentDetailRead:
    document = db.execute(
        select(Document)
        .where(Document.id == document_id, Document.workspace_id == workspace_id)
        .options(selectinload(Document.candidates))
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentDetailRead.model_validate(document)


@router.get("/{document_id}", response_model=DocumentDetailRead)
def get_document(
    document_id: int,
    membership: WorkspaceMembership = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> DocumentDetailRead:
    return _get_document_or_404(db, membership.workspace_id, document_id)


@router.post("", response_model=DocumentDetailRead, status_code=201)
async def upload_document(
    request: Request,
    source_id: int = Form(...),
    file: UploadFile = File(...),
    use_ai: bool = Form(default=False),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> DocumentDetailRead | JSONResponse:
    source = db.get(Source, source_id)
    if source is None or source.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Source not found in this workspace")
    raw = await file.read()
    try:
        document = process_document(
            db,
            workspace_id=membership.workspace_id,
            source=source,
            filename=file.filename,
            media_type=file.content_type,
            raw=raw,
            ai_settings=request.app.state.settings,
            use_ai=use_ai,
        )
    except (UnsupportedDocumentError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.flush()
    db.refresh(document)
    if document.status.value == "failed":
        return JSONResponse(
            status_code=422,
            content={"detail": document.error_message, "document_id": document.id},
        )
    return _get_document_or_404(db, membership.workspace_id, document.id)


@router.get("/{document_id}/candidates", response_model=list[CandidateRead])
def list_candidates(
    document_id: int,
    status: CandidateFindingStatus | None = Query(default=None),
    membership: WorkspaceMembership = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> list[CandidateRead]:
    document = db.get(Document, document_id)
    if document is None or document.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Document not found")
    stmt = select(CandidateFinding).where(
        CandidateFinding.workspace_id == membership.workspace_id,
        CandidateFinding.document_id == document_id,
    )
    if status is not None:
        stmt = stmt.where(CandidateFinding.status == status)
    rows = db.execute(stmt.order_by(CandidateFinding.id)).scalars()
    return [CandidateRead.model_validate(row) for row in rows]


def _decision_error(exc: Exception) -> HTTPException:
    if isinstance(exc, CandidateNotFoundError):
        return HTTPException(status_code=404, detail="Candidate not found")
    if isinstance(exc, CandidateAlreadyResolvedError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.post("/candidates/{candidate_id}/accept", response_model=CandidateRead)
def accept(
    candidate_id: int,
    payload: CandidateDecision,
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> CandidateRead:
    try:
        candidate = accept_candidate(
            db,
            workspace_id=membership.workspace_id,
            candidate_id=candidate_id,
            actor=membership.user.username,
            title=payload.title,
            severity=payload.severity,
        )
    except (CandidateNotFoundError, CandidateAlreadyResolvedError, ValueError) as exc:
        raise _decision_error(exc) from exc
    return CandidateRead.model_validate(candidate)


@router.post("/candidates/{candidate_id}/reject", response_model=CandidateRead)
def reject(
    candidate_id: int,
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> CandidateRead:
    try:
        candidate = reject_candidate(
            db,
            workspace_id=membership.workspace_id,
            candidate_id=candidate_id,
            actor=membership.user.username,
        )
    except (CandidateNotFoundError, CandidateAlreadyResolvedError) as exc:
        raise _decision_error(exc) from exc
    return CandidateRead.model_validate(candidate)
