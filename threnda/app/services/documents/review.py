"""Explicit accept/reject actions for extracted candidate findings."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.candidate_finding import CandidateFinding
from threnda.app.models.document import Document
from threnda.app.models.enums import CandidateFindingStatus, SeverityLevel
from threnda.app.models.finding import Finding
from threnda.app.models.mixins import utcnow
from threnda.app.services.ingestion.base import NormalizedFinding, ParsedImport
from threnda.app.services.ingestion.fingerprint import compute_fingerprint
from threnda.app.services.ingestion.runner import persist_parsed_import


class CandidateNotFoundError(LookupError):
    pass


class CandidateAlreadyResolvedError(RuntimeError):
    pass


def _pending(db: Session, workspace_id: int, candidate_id: int) -> CandidateFinding:
    candidate = db.get(CandidateFinding, candidate_id)
    if candidate is None or candidate.workspace_id != workspace_id:
        raise CandidateNotFoundError
    if candidate.status is not CandidateFindingStatus.PENDING:
        raise CandidateAlreadyResolvedError(
            f"Candidate is already {candidate.status.value}; the decision is immutable."
        )
    return candidate


def accept_candidate(
    db: Session,
    *,
    workspace_id: int,
    candidate_id: int,
    actor: str,
    title: str | None = None,
    severity: SeverityLevel | None = None,
) -> CandidateFinding:
    candidate = _pending(db, workspace_id, candidate_id)
    document = db.get(Document, candidate.document_id)
    if document is None or document.workspace_id != workspace_id:
        raise CandidateNotFoundError
    chosen_title = (title or candidate.extracted_title).strip()
    if not chosen_title or len(chosen_title) > 500:
        raise ValueError("title must contain 1 to 500 characters")
    chosen_severity = severity or candidate.extracted_severity
    if chosen_severity is None:
        raise ValueError("severity must be selected before accepting this candidate")

    source_identity = f"document:{document.file_hash}:candidate:{candidate.id}"
    normalized = NormalizedFinding(
        source_finding_id=source_identity,
        fingerprint=compute_fingerprint(None, None, chosen_title),
        title=chosen_title,
        severity=chosen_severity,
        description=candidate.excerpt,
        evidence=candidate.excerpt,
        raw_data={
            "document_id": document.id,
            "candidate_id": candidate.id,
            "provenance": {
                "page": candidate.page_number,
                "line_start": candidate.line_start,
                "line_end": candidate.line_end,
            },
            "ai_assisted": candidate.ai_assisted,
            "ai_model": candidate.ai_model,
        },
    )
    raw = json.dumps(normalized.raw_data, sort_keys=True).encode()
    persist_parsed_import(
        db,
        workspace_id=workspace_id,
        source_id=document.source_id,
        filename=f"accepted-{document.filename}",
        raw=raw,
        parsed=ParsedImport(findings=(normalized,), errors=(), total_records=1),
        infer_resolved=False,
    )
    finding = db.execute(
        select(Finding).where(
            Finding.workspace_id == workspace_id,
            Finding.source_id == document.source_id,
            Finding.source_finding_id == source_identity,
        )
    ).scalar_one()
    candidate.status = CandidateFindingStatus.ACCEPTED
    candidate.resulting_finding_id = finding.id
    candidate.resolved_at = utcnow()
    candidate.resolved_by = actor[:200]
    db.flush()
    return candidate


def reject_candidate(
    db: Session, *, workspace_id: int, candidate_id: int, actor: str
) -> CandidateFinding:
    candidate = _pending(db, workspace_id, candidate_id)
    candidate.status = CandidateFindingStatus.REJECTED
    candidate.resolved_at = utcnow()
    candidate.resolved_by = actor[:200]
    db.flush()
    return candidate
