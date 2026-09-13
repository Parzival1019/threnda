"""Persist local extraction results and review candidates."""

from __future__ import annotations

from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.core.config import Settings
from threnda.app.models.candidate_finding import CandidateFinding
from threnda.app.models.document import Document
from threnda.app.models.enums import DocumentContentType, DocumentStatus, SourceType
from threnda.app.models.source import Source
from threnda.app.services.ai import AIProvider, AIUnavailableError, build_ai_provider
from threnda.app.services.documents.extraction import (
    DocumentExtractionError,
    extract_text_from_pdf,
    extract_text_from_plain,
)
from threnda.app.services.documents.section_detection import detect_candidates
from threnda.app.services.ingestion.upload import sanitize_filename


class UnsupportedDocumentError(ValueError):
    pass


def _resolve_ai_provider(
    ai_provider: AIProvider | None, ai_settings: Settings | None
) -> AIProvider:
    """Resolve which provider ``use_ai=True`` should call.

    Building the provider can itself fail (a misconfigured
    ``THRENDA_AI_MODEL``) — that is reported as ``AIUnavailableError``,
    exactly like an unreachable Ollama instance, so the caller only ever
    needs to handle one exception type.
    """
    if ai_provider is not None:
        return ai_provider
    if ai_settings is None:
        raise ValueError("ai_settings is required when use_ai=True and no ai_provider is given")
    return build_ai_provider(ai_settings)


def _content_type(filename: str, raw: bytes, media_type: str | None) -> DocumentContentType:
    lower = filename.lower()
    if raw.startswith(b"%PDF-") or lower.endswith(".pdf") or media_type == "application/pdf":
        if not raw.startswith(b"%PDF-"):
            raise UnsupportedDocumentError(
                "The uploaded file is labelled PDF but has no PDF signature"
            )
        return DocumentContentType.PDF
    if lower.endswith((".txt", ".md")) or (media_type or "").startswith("text/"):
        return DocumentContentType.TEXT
    raise UnsupportedDocumentError("Only PDF, TXT, and Markdown documents are accepted")


def process_document(
    db: Session,
    *,
    workspace_id: int,
    source: Source,
    filename: str | None,
    media_type: str | None,
    raw: bytes,
    ai_provider: AIProvider | None = None,
    ai_settings: Settings | None = None,
    use_ai: bool = False,
) -> Document:
    """Extract and persist a document; repeated identical uploads return one row.

    The AI provider is only ever constructed when ``use_ai`` is true and no
    ``ai_provider`` override was already supplied (tests inject one
    directly) — never eagerly from ``ai_settings`` on every call, so a
    misconfigured ``THRENDA_AI_MODEL`` only affects uploads that actually
    asked for AI assistance, not every document upload.
    """
    if source.workspace_id != workspace_id or source.source_type is not SourceType.PENTEST_REPORT:
        raise UnsupportedDocumentError("Document review requires a pentest_report source")
    safe_name = sanitize_filename(filename)
    digest = sha256(raw).hexdigest()
    existing = db.execute(
        select(Document).where(
            Document.workspace_id == workspace_id,
            Document.source_id == source.id,
            Document.file_hash == digest,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    kind = _content_type(safe_name, raw, media_type)
    try:
        extracted = (
            extract_text_from_pdf(raw)
            if kind is DocumentContentType.PDF
            else extract_text_from_plain(raw)
        )
    except DocumentExtractionError as exc:
        document = Document(
            workspace_id=workspace_id,
            source_id=source.id,
            filename=safe_name,
            content_type=kind,
            file_hash=digest,
            status=DocumentStatus.FAILED,
            error_message=str(exc),
            extracted_text="",
        )
        db.add(document)
        db.flush()
        return document

    document = Document(
        workspace_id=workspace_id,
        source_id=source.id,
        filename=safe_name,
        content_type=kind,
        file_hash=digest,
        status=DocumentStatus.PROCESSED,
        error_message=(
            "Extracted text was truncated at the configured safety limit."
            if extracted.truncated
            else None
        ),
        extracted_text=extracted.full_text,
        page_count=extracted.page_count,
    )
    db.add(document)
    db.flush()

    for draft in detect_candidates(extracted):
        db.add(
            CandidateFinding(
                workspace_id=workspace_id,
                document_id=document.id,
                extracted_title=draft.title,
                extracted_severity=draft.severity,
                excerpt=draft.excerpt,
                page_number=draft.page_number,
                line_start=draft.line_start,
                line_end=draft.line_end,
                confidence=draft.confidence,
                validation_issues=list(draft.validation_issues),
            )
        )

    if use_ai:
        try:
            provider = _resolve_ai_provider(ai_provider, ai_settings)
            ai_candidates = provider.extract_findings_from_text(extracted.full_text)
        except AIUnavailableError as exc:
            document.error_message = f"Optional local AI unavailable: {exc}"
        else:
            for candidate in ai_candidates:
                db.add(
                    CandidateFinding(
                        workspace_id=workspace_id,
                        document_id=document.id,
                        extracted_title=candidate.title,
                        extracted_severity=candidate.severity,
                        excerpt=candidate.excerpt,
                        confidence=candidate.confidence,
                        validation_issues=["machine-generated suggestion; human review required"],
                        ai_assisted=True,
                        ai_model=candidate.model,
                    )
                )
    db.flush()
    return document
