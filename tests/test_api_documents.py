"""End-to-end tests for the /api/v1/documents JSON API: local extraction,
listing, and the explicit accept/reject candidate decisions."""

from __future__ import annotations

import itertools
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from threnda.app.models.enums import SeverityLevel, SourceType
from threnda.app.models.finding import Finding
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace

DEMO_DATA = Path(__file__).resolve().parents[1] / "demo-data"
_WORKSPACE_COUNTER = itertools.count()


def _make_workspace_and_source(
    db_session: Session, source_type: SourceType = SourceType.PENTEST_REPORT
) -> tuple[int, int]:
    n = next(_WORKSPACE_COUNTER)
    workspace = Workspace(name=f"synthetic-docs-api-{source_type.value}-{n}")
    db_session.add(workspace)
    db_session.flush()
    source = Source(workspace_id=workspace.id, name="pentest-source", source_type=source_type)
    db_session.add(source)
    db_session.commit()
    return workspace.id, source.id


def test_upload_txt_document_happy_path(client: TestClient, db_session: Session) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session)
    content = (DEMO_DATA / "documents" / "synthetic-pentest-report.txt").read_bytes()

    response = client.post(
        "/api/v1/documents",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("report.txt", content, "text/plain")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "processed"
    assert body["content_type"] == "text"
    assert len(body["candidates"]) >= 1
    assert all(c["status"] == "pending" for c in body["candidates"])


def test_upload_pdf_document_happy_path(client: TestClient, db_session: Session) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session)
    content = (DEMO_DATA / "documents" / "synthetic-pentest-report.pdf").read_bytes()

    response = client.post(
        "/api/v1/documents",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("report.pdf", content, "application/pdf")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["content_type"] == "pdf"
    assert body["page_count"] and body["page_count"] > 0


def test_upload_document_rejects_non_pentest_report_source(
    client: TestClient, db_session: Session
) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session, SourceType.JSON)

    response = client.post(
        "/api/v1/documents",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("report.txt", b"Finding: X\nSeverity: High\n", "text/plain")},
    )

    assert response.status_code == 422


def test_upload_document_rejects_unknown_source(client: TestClient, db_session: Session) -> None:
    workspace_id, _source_id = _make_workspace_and_source(db_session)

    response = client.post(
        "/api/v1/documents",
        params={"workspace_id": workspace_id},
        data={"source_id": "999999"},
        files={"file": ("report.txt", b"content", "text/plain")},
    )

    assert response.status_code == 404


def test_upload_document_rejects_unsupported_file_type(
    client: TestClient, db_session: Session
) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session)

    response = client.post(
        "/api/v1/documents",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("report.docx", b"binary content", "application/msword")},
    )

    assert response.status_code == 422


def test_upload_malformed_pdf_returns_a_failed_document(
    client: TestClient, db_session: Session
) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session)

    response = client.post(
        "/api/v1/documents",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("broken.pdf", b"%PDF-1.4\nnot a real pdf body", "application/pdf")},
    )

    assert response.status_code == 422
    body = response.json()
    assert "document_id" in body


def test_repeated_identical_upload_is_idempotent(client: TestClient, db_session: Session) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session)
    content = (DEMO_DATA / "documents" / "synthetic-pentest-report.txt").read_bytes()

    first = client.post(
        "/api/v1/documents",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("report.txt", content, "text/plain")},
    )
    second = client.post(
        "/api/v1/documents",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("report-again.txt", content, "text/plain")},
    )

    assert first.json()["id"] == second.json()["id"]
    listing = client.get("/api/v1/documents", params={"workspace_id": workspace_id})
    assert len(listing.json()) == 1


def test_use_ai_with_ai_disabled_still_returns_heuristic_candidates(
    client: TestClient, db_session: Session
) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session)
    content = (DEMO_DATA / "documents" / "synthetic-pentest-report.txt").read_bytes()

    response = client.post(
        "/api/v1/documents",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id), "use_ai": "true"},
        files={"file": ("report.txt", content, "text/plain")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["error_message"] is not None
    assert "unavailable" in body["error_message"]
    assert all(c["ai_assisted"] is False for c in body["candidates"])


def test_get_document_404s_for_another_workspace(client: TestClient, db_session: Session) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session)
    content = (DEMO_DATA / "documents" / "synthetic-pentest-report.txt").read_bytes()
    upload = client.post(
        "/api/v1/documents",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("report.txt", content, "text/plain")},
    )
    document_id = upload.json()["id"]
    other_workspace_id, _ = _make_workspace_and_source(db_session)

    response = client.get(
        f"/api/v1/documents/{document_id}", params={"workspace_id": other_workspace_id}
    )
    assert response.status_code == 404


def test_list_candidates_filters_by_status(client: TestClient, db_session: Session) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session)
    content = (DEMO_DATA / "documents" / "synthetic-pentest-report.txt").read_bytes()
    upload = client.post(
        "/api/v1/documents",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("report.txt", content, "text/plain")},
    )
    document_id = upload.json()["id"]

    response = client.get(
        f"/api/v1/documents/{document_id}/candidates",
        params={"workspace_id": workspace_id, "status": "pending"},
    )
    assert response.status_code == 200
    assert len(response.json()) >= 1

    accepted = client.get(
        f"/api/v1/documents/{document_id}/candidates",
        params={"workspace_id": workspace_id, "status": "accepted"},
    )
    assert accepted.json() == []


def _upload_and_get_first_candidate(
    client: TestClient, workspace_id: int, source_id: int
) -> dict[str, object]:
    content = (DEMO_DATA / "documents" / "synthetic-pentest-report.txt").read_bytes()
    upload = client.post(
        "/api/v1/documents",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("report.txt", content, "text/plain")},
    )
    return dict(upload.json()["candidates"][0])


def test_accept_candidate_creates_a_finding(client: TestClient, db_session: Session) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session)
    candidate = _upload_and_get_first_candidate(client, workspace_id, source_id)

    response = client.post(
        f"/api/v1/documents/candidates/{candidate['id']}/accept",
        params={"workspace_id": workspace_id},
        json={"severity": "high", "actor": "api-test"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["resulting_finding_id"] is not None

    finding = db_session.get(Finding, body["resulting_finding_id"])
    assert finding is not None


def test_accept_candidate_twice_returns_409(client: TestClient, db_session: Session) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session)
    candidate = _upload_and_get_first_candidate(client, workspace_id, source_id)

    client.post(
        f"/api/v1/documents/candidates/{candidate['id']}/accept",
        params={"workspace_id": workspace_id},
        json={"severity": "high"},
    )
    response = client.post(
        f"/api/v1/documents/candidates/{candidate['id']}/accept",
        params={"workspace_id": workspace_id},
        json={"severity": "high"},
    )

    assert response.status_code == 409


def test_reject_candidate(client: TestClient, db_session: Session) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session)
    candidate = _upload_and_get_first_candidate(client, workspace_id, source_id)

    response = client.post(
        f"/api/v1/documents/candidates/{candidate['id']}/reject",
        params={"workspace_id": workspace_id},
        json={},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_accept_candidate_from_another_workspace_is_404(
    client: TestClient, db_session: Session
) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session)
    candidate = _upload_and_get_first_candidate(client, workspace_id, source_id)
    other_workspace_id, _ = _make_workspace_and_source(db_session)

    response = client.post(
        f"/api/v1/documents/candidates/{candidate['id']}/accept",
        params={"workspace_id": other_workspace_id},
        json={"severity": "high"},
    )

    assert response.status_code == 404


def test_accept_candidate_missing_severity_when_none_extracted_is_422(
    client: TestClient, db_session: Session
) -> None:
    """The document sample's third finding has no severity/CVSS line
    detected — accepting it without choosing a severity must be a 422, not
    a silently-guessed value."""
    workspace_id, source_id = _make_workspace_and_source(db_session)
    content = (DEMO_DATA / "documents" / "synthetic-pentest-report.txt").read_bytes()
    upload = client.post(
        "/api/v1/documents",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("report.txt", content, "text/plain")},
    )
    no_severity_candidates = [
        c for c in upload.json()["candidates"] if c["extracted_severity"] is None
    ]
    assert no_severity_candidates, "fixture must contain at least one no-severity candidate"

    response = client.post(
        f"/api/v1/documents/candidates/{no_severity_candidates[0]['id']}/accept",
        params={"workspace_id": workspace_id},
        json={},
    )

    assert response.status_code == 422


def test_accepting_one_candidate_does_not_resolve_unrelated_findings(
    client: TestClient, db_session: Session
) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session)
    unrelated = Finding(
        workspace_id=workspace_id,
        source_id=source_id,
        fingerprint="unrelated",
        source_finding_id="unrelated-1",
        title="Unrelated pre-existing finding",
        severity=SeverityLevel.MEDIUM,
        references=[],
        raw_data={},
    )
    db_session.add(unrelated)
    db_session.commit()

    candidate = _upload_and_get_first_candidate(client, workspace_id, source_id)
    client.post(
        f"/api/v1/documents/candidates/{candidate['id']}/accept",
        params={"workspace_id": workspace_id},
        json={"severity": "high"},
    )

    db_session.refresh(unrelated)
    assert unrelated.resolved_at is None


def test_list_documents_query_scopes_to_workspace(client: TestClient, db_session: Session) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session)
    other_workspace_id, other_source_id = _make_workspace_and_source(db_session)
    content = (DEMO_DATA / "documents" / "synthetic-pentest-report.txt").read_bytes()

    client.post(
        "/api/v1/documents",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("a.txt", content, "text/plain")},
    )
    client.post(
        "/api/v1/documents",
        params={"workspace_id": other_workspace_id},
        data={"source_id": str(other_source_id)},
        files={"file": ("b.txt", content, "text/plain")},
    )

    response = client.get("/api/v1/documents", params={"workspace_id": workspace_id})
    assert len(response.json()) == 1
