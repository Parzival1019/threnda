"""Tests for the server-rendered /documents review page: local extraction,
CSRF-protected accept/reject, and workspace isolation."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from webhelpers import bootstrap_workspace, extract_csrf

from threnda.app.models.finding import Finding

DEMO_DATA = Path(__file__).resolve().parents[1] / "demo-data"


def _create_pentest_source(client: TestClient, name: str = "web-pentest") -> int:
    response = client.get("/sources")
    token = extract_csrf(response.text)
    response = client.post(
        "/sources",
        data={"csrf_token": token, "name": name, "source_type": "pentest_report"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    match = re.search(rf"<option value=\"(\d+)\">{name}", client.get("/documents").text)
    assert match is not None
    return int(match.group(1))


def test_documents_page_prompts_for_a_source_when_none_exists(client: TestClient) -> None:
    bootstrap_workspace(client)
    response = client.get("/documents")
    assert response.status_code == 200
    assert "No pentest-report source" in response.text


def test_upload_txt_document_shows_candidates_for_review(client: TestClient) -> None:
    bootstrap_workspace(client)
    source_id = _create_pentest_source(client)
    content = (DEMO_DATA / "documents" / "synthetic-pentest-report.txt").read_bytes()

    page = client.get("/documents")
    token = extract_csrf(page.text)
    response = client.post(
        "/documents",
        data={"csrf_token": token, "source_id": str(source_id)},
        files={"file": ("report.txt", content, "text/plain")},
    )

    assert response.status_code == 200
    assert "review candidate" in response.text
    assert "HEURISTIC" in response.text
    assert "report.txt" in response.text


def test_upload_with_non_integer_source_id_is_a_422_not_a_crash(client: TestClient) -> None:
    bootstrap_workspace(client)
    _create_pentest_source(client)
    page = client.get("/documents")
    token = extract_csrf(page.text)

    response = client.post(
        "/documents",
        data={"csrf_token": token, "source_id": "not-a-number"},
        files={"file": ("report.txt", b"content", "text/plain")},
    )

    assert response.status_code == 422


def test_upload_missing_csrf_token_is_rejected(client: TestClient) -> None:
    bootstrap_workspace(client)
    source_id = _create_pentest_source(client)
    client.get("/documents")

    response = client.post(
        "/documents",
        data={"csrf_token": "wrong-token", "source_id": str(source_id)},
        files={"file": ("report.txt", b"Finding: X\nSeverity: High\n", "text/plain")},
    )

    assert response.status_code == 403


def _upload_and_find_first_candidate_id(client: TestClient, source_id: int) -> int:
    content = (DEMO_DATA / "documents" / "synthetic-pentest-report.txt").read_bytes()
    page = client.get("/documents")
    token = extract_csrf(page.text)
    response = client.post(
        "/documents",
        data={"csrf_token": token, "source_id": str(source_id)},
        files={"file": ("report.txt", content, "text/plain")},
    )
    match = re.search(r"/documents/candidates/(\d+)/accept", response.text)
    assert match is not None
    return int(match.group(1))


def test_accept_candidate_creates_a_finding_and_removes_it_from_pending(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    source_id = _create_pentest_source(client)
    candidate_id = _upload_and_find_first_candidate_id(client, source_id)

    page = client.get("/documents")
    token = extract_csrf(page.text)
    response = client.post(
        f"/documents/candidates/{candidate_id}/accept",
        data={"csrf_token": token, "title": "Reviewed title", "severity": "high"},
        follow_redirects=False,
    )

    assert response.status_code == 303

    findings = db_session.scalars(select(Finding).where(Finding.workspace_id == workspace_id)).all()
    assert len(findings) == 1
    assert findings[0].title == "Reviewed title"

    listing = client.get("/documents")
    assert f"/documents/candidates/{candidate_id}/accept" not in listing.text


def test_reject_candidate_removes_it_from_pending(client: TestClient) -> None:
    bootstrap_workspace(client)
    source_id = _create_pentest_source(client)
    candidate_id = _upload_and_find_first_candidate_id(client, source_id)

    page = client.get("/documents")
    token = extract_csrf(page.text)
    response = client.post(
        f"/documents/candidates/{candidate_id}/reject",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    listing = client.get("/documents")
    assert f"/documents/candidates/{candidate_id}/reject" not in listing.text


def test_accept_candidate_with_wrong_csrf_token_is_rejected(client: TestClient) -> None:
    bootstrap_workspace(client)
    source_id = _create_pentest_source(client)
    candidate_id = _upload_and_find_first_candidate_id(client, source_id)

    client.get("/documents")
    response = client.post(
        f"/documents/candidates/{candidate_id}/accept",
        data={"csrf_token": "totally-wrong", "title": "x", "severity": "high"},
    )

    assert response.status_code == 403


def test_accept_candidate_from_another_workspace_is_404(client: TestClient) -> None:
    bootstrap_workspace(client, name="ws-a")
    source_id = _create_pentest_source(client, name="ws-a-pentest")
    candidate_id = _upload_and_find_first_candidate_id(client, source_id)

    bootstrap_workspace(client, name="ws-b")
    client.get("/documents")
    token = client.cookies.get("threnda_csrf")
    assert token is not None
    response = client.post(
        f"/documents/candidates/{candidate_id}/accept",
        data={"csrf_token": token, "title": "x", "severity": "high"},
    )

    assert response.status_code == 404


def test_accept_candidate_twice_is_a_409(client: TestClient) -> None:
    bootstrap_workspace(client)
    source_id = _create_pentest_source(client)
    candidate_id = _upload_and_find_first_candidate_id(client, source_id)

    page = client.get("/documents")
    token = extract_csrf(page.text)
    client.post(
        f"/documents/candidates/{candidate_id}/accept",
        data={"csrf_token": token, "title": "x", "severity": "high"},
    )

    page2 = client.get("/documents")
    token2 = extract_csrf(page2.text)
    response = client.post(
        f"/documents/candidates/{candidate_id}/accept",
        data={"csrf_token": token2, "title": "x", "severity": "high"},
    )

    assert response.status_code == 409


def test_sidebar_document_nav_shows_pending_candidate_count(client: TestClient) -> None:
    bootstrap_workspace(client)
    source_id = _create_pentest_source(client)

    before = client.get("/documents")
    assert 'id="nav-document-count">0<' in before.text

    content = (DEMO_DATA / "documents" / "synthetic-pentest-report.txt").read_bytes()
    token = extract_csrf(before.text)
    client.post(
        "/documents",
        data={"csrf_token": token, "source_id": str(source_id)},
        files={"file": ("report.txt", content, "text/plain")},
    )

    after = client.get("/documents")
    assert 'id="nav-document-count">0<' not in after.text
