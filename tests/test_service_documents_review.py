"""Tests for explicit accept/reject candidate-finding decisions.

See docs/pentest-and-ai.md, "Mandatory human review": a decision is
immutable once made, and accepting one candidate must never touch any
other, unrelated finding.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.candidate_finding import CandidateFinding
from threnda.app.models.document import Document
from threnda.app.models.enums import (
    CandidateFindingStatus,
    DocumentContentType,
    DocumentStatus,
    SeverityLevel,
    SourceType,
)
from threnda.app.models.finding import Finding
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace
from threnda.app.services.documents.review import (
    CandidateAlreadyResolvedError,
    CandidateNotFoundError,
    accept_candidate,
    reject_candidate,
)


def _seed(db: Session) -> tuple[Workspace, Source, Document, CandidateFinding]:
    workspace = Workspace(name=f"synthetic-review-{id(db)}")
    db.add(workspace)
    db.flush()
    source = Source(
        workspace_id=workspace.id, name="pentest-source", source_type=SourceType.PENTEST_REPORT
    )
    db.add(source)
    db.flush()
    document = Document(
        workspace_id=workspace.id,
        source_id=source.id,
        filename="report.txt",
        content_type=DocumentContentType.TEXT,
        file_hash="a" * 64,
        status=DocumentStatus.PROCESSED,
        extracted_text="Finding: X\nSeverity: High\nSome long enough excerpt text here.",
    )
    db.add(document)
    db.flush()
    candidate = CandidateFinding(
        workspace_id=workspace.id,
        document_id=document.id,
        extracted_title="Draft finding title",
        extracted_severity=SeverityLevel.HIGH,
        excerpt="Some long enough excerpt text here.",
        confidence=0.8,
        validation_issues=[],
    )
    db.add(candidate)
    db.flush()
    return workspace, source, document, candidate


def test_accept_candidate_creates_a_finding_and_marks_the_candidate_accepted(
    db_session: Session,
) -> None:
    workspace, source, document, candidate = _seed(db_session)

    result = accept_candidate(
        db_session, workspace_id=workspace.id, candidate_id=candidate.id, actor="tester"
    )

    assert result.status is CandidateFindingStatus.ACCEPTED
    assert result.resulting_finding_id is not None
    assert result.resolved_by == "tester"
    assert result.resolved_at is not None

    finding = db_session.get(Finding, result.resulting_finding_id)
    assert finding is not None
    assert finding.title == "Draft finding title"
    assert finding.severity is SeverityLevel.HIGH
    assert finding.source_id == document.source_id
    assert finding.raw_data["candidate_id"] == candidate.id
    assert finding.raw_data["document_id"] == document.id


def test_accept_candidate_allows_overriding_title_and_severity(db_session: Session) -> None:
    workspace, _source, _document, candidate = _seed(db_session)

    result = accept_candidate(
        db_session,
        workspace_id=workspace.id,
        candidate_id=candidate.id,
        actor="tester",
        title="Reviewer-edited title",
        severity=SeverityLevel.CRITICAL,
    )

    finding = db_session.get(Finding, result.resulting_finding_id)
    assert finding is not None
    assert finding.title == "Reviewer-edited title"
    assert finding.severity is SeverityLevel.CRITICAL


def test_accept_candidate_requires_a_severity_when_none_was_extracted(db_session: Session) -> None:
    workspace, _source, document, _candidate = _seed(db_session)
    candidate = CandidateFinding(
        workspace_id=workspace.id,
        document_id=document.id,
        extracted_title="No severity detected",
        extracted_severity=None,
        excerpt="Some long enough excerpt text here without any severity keyword.",
        confidence=0.2,
        validation_issues=["no severity or CVSS keyword detected in this section"],
    )
    db_session.add(candidate)
    db_session.flush()

    with pytest.raises(ValueError, match="severity"):
        accept_candidate(
            db_session, workspace_id=workspace.id, candidate_id=candidate.id, actor="t"
        )


def test_accept_candidate_rejects_an_empty_title(db_session: Session) -> None:
    workspace, _source, _document, candidate = _seed(db_session)

    with pytest.raises(ValueError, match="title"):
        accept_candidate(
            db_session,
            workspace_id=workspace.id,
            candidate_id=candidate.id,
            actor="t",
            title="   ",
        )


def test_accept_candidate_twice_is_rejected_as_immutable(db_session: Session) -> None:
    workspace, _source, _document, candidate = _seed(db_session)
    accept_candidate(db_session, workspace_id=workspace.id, candidate_id=candidate.id, actor="t")

    with pytest.raises(CandidateAlreadyResolvedError):
        accept_candidate(
            db_session, workspace_id=workspace.id, candidate_id=candidate.id, actor="t"
        )


def test_reject_candidate_twice_is_rejected_as_immutable(db_session: Session) -> None:
    workspace, _source, _document, candidate = _seed(db_session)
    reject_candidate(db_session, workspace_id=workspace.id, candidate_id=candidate.id, actor="t")

    with pytest.raises(CandidateAlreadyResolvedError):
        reject_candidate(
            db_session, workspace_id=workspace.id, candidate_id=candidate.id, actor="t"
        )


def test_accept_after_reject_is_rejected_as_immutable(db_session: Session) -> None:
    workspace, _source, _document, candidate = _seed(db_session)
    reject_candidate(db_session, workspace_id=workspace.id, candidate_id=candidate.id, actor="t")

    with pytest.raises(CandidateAlreadyResolvedError):
        accept_candidate(
            db_session, workspace_id=workspace.id, candidate_id=candidate.id, actor="t"
        )


def test_reject_candidate_marks_rejected_without_creating_a_finding(db_session: Session) -> None:
    workspace, _source, _document, candidate = _seed(db_session)

    result = reject_candidate(
        db_session, workspace_id=workspace.id, candidate_id=candidate.id, actor="tester"
    )

    assert result.status is CandidateFindingStatus.REJECTED
    assert result.resulting_finding_id is None
    findings = db_session.scalars(select(Finding).where(Finding.workspace_id == workspace.id)).all()
    assert findings == []


def test_accept_candidate_from_another_workspace_is_not_found(db_session: Session) -> None:
    _workspace, _source, _document, candidate = _seed(db_session)
    other_workspace = Workspace(name="other-workspace")
    db_session.add(other_workspace)
    db_session.flush()

    with pytest.raises(CandidateNotFoundError):
        accept_candidate(
            db_session, workspace_id=other_workspace.id, candidate_id=candidate.id, actor="t"
        )


def test_reject_candidate_from_another_workspace_is_not_found(db_session: Session) -> None:
    _workspace, _source, _document, candidate = _seed(db_session)
    other_workspace = Workspace(name="other-workspace-2")
    db_session.add(other_workspace)
    db_session.flush()

    with pytest.raises(CandidateNotFoundError):
        reject_candidate(
            db_session, workspace_id=other_workspace.id, candidate_id=candidate.id, actor="t"
        )


def test_nonexistent_candidate_is_not_found(db_session: Session) -> None:
    workspace, _source, _document, _candidate = _seed(db_session)

    with pytest.raises(CandidateNotFoundError):
        accept_candidate(db_session, workspace_id=workspace.id, candidate_id=999_999, actor="t")


def test_accepting_one_candidate_does_not_resolve_unrelated_findings_on_the_same_source(
    db_session: Session,
) -> None:
    """The accept flow calls persist_parsed_import with infer_resolved=False
    specifically so this can never happen — see
    threnda.app.services.documents.review.accept_candidate."""
    workspace, source, _document, candidate = _seed(db_session)

    unrelated = Finding(
        workspace_id=workspace.id,
        source_id=source.id,
        fingerprint="unrelated-fingerprint",
        source_finding_id="unrelated-1",
        title="Unrelated pre-existing finding",
        severity=SeverityLevel.MEDIUM,
        references=[],
        raw_data={},
    )
    db_session.add(unrelated)
    db_session.flush()
    assert unrelated.resolved_at is None

    accept_candidate(db_session, workspace_id=workspace.id, candidate_id=candidate.id, actor="t")

    db_session.refresh(unrelated)
    assert unrelated.resolved_at is None
    assert unrelated.title == "Unrelated pre-existing finding"


def test_accepting_a_second_candidate_from_the_same_document_does_not_disturb_the_first(
    db_session: Session,
) -> None:
    workspace, _source, document, first_candidate = _seed(db_session)
    second_candidate = CandidateFinding(
        workspace_id=workspace.id,
        document_id=document.id,
        extracted_title="Second draft finding",
        extracted_severity=SeverityLevel.LOW,
        excerpt="A second, unrelated, long enough excerpt of text here.",
        confidence=0.5,
        validation_issues=[],
    )
    db_session.add(second_candidate)
    db_session.flush()

    first_result = accept_candidate(
        db_session, workspace_id=workspace.id, candidate_id=first_candidate.id, actor="t"
    )
    second_result = accept_candidate(
        db_session, workspace_id=workspace.id, candidate_id=second_candidate.id, actor="t"
    )

    assert first_result.resulting_finding_id != second_result.resulting_finding_id
    db_session.refresh(first_candidate)
    assert first_candidate.status is CandidateFindingStatus.ACCEPTED
