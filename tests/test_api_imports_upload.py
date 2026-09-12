"""End-to-end tests for POST /api/v1/imports — the real HTTP upload path.

Uses the synthetic demo-data fixtures for the happy paths (the same files a
new user would upload after following the README), and hand-built payloads
for the edge cases the independent Milestone 2 acceptance checklist calls
out explicitly (workspace isolation, malicious filenames, malformed
content, CVSS bounds, mapping persistence).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.core.config import Settings
from threnda.app.main import create_app
from threnda.app.models.asset import Asset
from threnda.app.models.enums import SourceType
from threnda.app.models.finding import Finding
from threnda.app.models.import_run import ImportRun
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace

DEMO_DATA = Path(__file__).resolve().parents[1] / "demo-data"

CSV_MAPPING = {
    "Vulnerability": "title",
    "Host": "asset",
    "Severity": "severity",
    "CVSS": "cvss",
    "CVE": "cve",
    "Description": "description",
    "Solution": "remediation",
}


def _make_workspace_and_source(db_session: Session, source_type: SourceType) -> tuple[int, int]:
    workspace = Workspace(name=f"synthetic-{source_type.value}")
    db_session.add(workspace)
    db_session.flush()
    source = Source(
        workspace_id=workspace.id,
        name=f"synthetic-{source_type.value}-source",
        source_type=source_type,
    )
    db_session.add(source)
    db_session.commit()
    return workspace.id, source.id


def test_upload_csv_happy_path(client: TestClient, db_session: Session) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session, SourceType.CSV)
    content = (DEMO_DATA / "csv" / "synthetic-vulnerabilities.csv").read_bytes()

    response = client.post(
        "/api/v1/imports",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id), "csv_mapping": json.dumps(CSV_MAPPING)},
        files={"file": ("synthetic-vulnerabilities.csv", content, "text/csv")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["import_run"]["status"] == "completed"
    assert body["import_run"]["successful_records"] == 5
    assert body["import_run"]["failed_records"] == 0
    assert body["row_errors"] == []

    findings = db_session.scalars(select(Finding).where(Finding.workspace_id == workspace_id)).all()
    assert len(findings) == 5
    assets = db_session.scalars(select(Asset).where(Asset.workspace_id == workspace_id)).all()
    assert {a.name for a in assets} == {
        "synthetic-web-01.example.test",
        "synthetic-app-02.example.test",
        "synthetic-web-03.example.test",
    }


def test_upload_json_happy_path(client: TestClient, db_session: Session) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session, SourceType.JSON)
    content = (DEMO_DATA / "json" / "synthetic-findings.json").read_bytes()

    response = client.post(
        "/api/v1/imports",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("synthetic-findings.json", content, "application/json")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["import_run"]["successful_records"] == 3
    assert body["import_run"]["failed_records"] == 0


def test_upload_sarif_happy_path(client: TestClient, db_session: Session) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session, SourceType.SARIF)
    content = (DEMO_DATA / "sarif" / "synthetic-scan.sarif.json").read_bytes()

    response = client.post(
        "/api/v1/imports",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("synthetic-scan.sarif.json", content, "application/sarif+json")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    # 3 fail-kind results across two runs; one pass-kind result excluded.
    assert body["import_run"]["successful_records"] == 3
    assert body["import_run"]["total_records"] == 4


def test_upload_rejects_foreign_workspace_without_writing_anything(
    client: TestClient, db_session: Session
) -> None:
    _workspace_id, source_id = _make_workspace_and_source(db_session, SourceType.JSON)
    other_workspace = Workspace(name="synthetic-other")
    db_session.add(other_workspace)
    db_session.commit()

    response = client.post(
        "/api/v1/imports",
        params={"workspace_id": other_workspace.id},
        data={"source_id": str(source_id)},
        files={"file": ("f.json", b'{"findings": []}', "application/json")},
    )

    assert response.status_code == 404
    assert db_session.scalars(select(ImportRun)).all() == []
    assert db_session.scalars(select(Finding)).all() == []


def test_upload_rejects_unknown_source_id(client: TestClient, db_session: Session) -> None:
    workspace = Workspace(name="synthetic-lonely")
    db_session.add(workspace)
    db_session.commit()

    response = client.post(
        "/api/v1/imports",
        params={"workspace_id": workspace.id},
        data={"source_id": "999999"},
        files={"file": ("f.json", b'{"findings": []}', "application/json")},
    )

    assert response.status_code == 404


def test_malicious_filename_is_sanitized_not_used_as_a_path(
    client: TestClient, db_session: Session
) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session, SourceType.JSON)

    response = client.post(
        "/api/v1/imports",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={
            "file": (
                "../../../../etc/passwd",
                b'{"findings": [{"id": "f1", "title": "T", "severity": "low"}]}',
                "application/json",
            )
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["import_run"]["filename"] == "passwd"


def test_sanitize_filename_falls_back_to_a_placeholder_when_empty_or_missing() -> None:
    """httpx's own multipart encoder won't even construct a file part with
    an empty filename (it degrades to a plain form field), so this is
    exercised directly rather than through a full HTTP round trip."""
    from threnda.app.api.v1.imports import _sanitize_filename

    assert _sanitize_filename(None) == "upload"
    assert _sanitize_filename("") == "upload"
    assert _sanitize_filename("   ") == "upload"


def test_malformed_json_records_a_failed_import_run_without_findings(
    client: TestClient, db_session: Session
) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session, SourceType.JSON)

    response = client.post(
        "/api/v1/imports",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("broken.json", b"{not valid json", "application/json")},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["import_run"]["status"] == "failed"
    assert "detail" in body

    # The FAILED ImportRun is a durable audit entry, not rolled back.
    import_runs = db_session.scalars(
        select(ImportRun).where(ImportRun.workspace_id == workspace_id)
    ).all()
    assert len(import_runs) == 1
    assert import_runs[0].status.value == "failed"
    assert db_session.scalars(select(Finding)).all() == []


def test_content_not_matching_declared_source_type_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session, SourceType.CSV)

    response = client.post(
        "/api/v1/imports",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id), "csv_mapping": json.dumps(CSV_MAPPING)},
        files={
            "file": ("scan.sarif.json", b'{"runs": [{"tool": {"driver": {}}}]}', "application/json")
        },
    )

    assert response.status_code == 422
    assert "does not look like" in response.json()["detail"]


def test_out_of_range_cvss_is_a_partial_success_with_row_errors(
    client: TestClient, db_session: Session
) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session, SourceType.JSON)
    payload = {
        "findings": [
            {"id": "good", "title": "Good finding", "severity": "low"},
            {"id": "bad-cvss", "title": "Bad finding", "cvss": 42},
        ]
    }

    response = client.post(
        "/api/v1/imports",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("f.json", json.dumps(payload).encode(), "application/json")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["import_run"]["successful_records"] == 1
    assert body["import_run"]["failed_records"] == 1
    assert body["import_run"]["status"] == "completed"
    assert len(body["row_errors"]) == 1
    assert body["row_errors"][0]["index"] == 1


def test_csv_upload_without_mapping_and_none_saved_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session, SourceType.CSV)

    response = client.post(
        "/api/v1/imports",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("f.csv", b"Vulnerability\r\nT\r\n", "text/csv")},
    )

    assert response.status_code == 422
    assert "mapping" in response.json()["detail"].lower()


def test_csv_mapping_is_saved_and_reused_on_a_later_upload(
    client: TestClient, db_session: Session
) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session, SourceType.CSV)
    content = (DEMO_DATA / "csv" / "synthetic-vulnerabilities.csv").read_bytes()

    first = client.post(
        "/api/v1/imports",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id), "csv_mapping": json.dumps(CSV_MAPPING)},
        files={"file": ("a.csv", content, "text/csv")},
    )
    assert first.status_code == 201, first.text

    second = client.post(
        "/api/v1/imports",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("b.csv", content, "text/csv")},
    )

    assert second.status_code == 201, second.text
    assert second.json()["import_run"]["successful_records"] == 5


def test_csv_mapping_rejects_unknown_target_field(client: TestClient, db_session: Session) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session, SourceType.CSV)

    response = client.post(
        "/api/v1/imports",
        params={"workspace_id": workspace_id},
        data={
            "source_id": str(source_id),
            "csv_mapping": json.dumps({"Vulnerability": "title", "Extra": "not_a_real_field"}),
        },
        files={"file": ("f.csv", b"Vulnerability,Extra\r\nT,x\r\n", "text/csv")},
    )

    assert response.status_code == 422


def test_upload_to_unsupported_source_type_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    workspace_id, source_id = _make_workspace_and_source(db_session, SourceType.TRIVY)

    response = client.post(
        "/api/v1/imports",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("f.json", b'{"findings": []}', "application/json")},
    )

    assert response.status_code == 422
    assert "not supported" in response.json()["detail"]


def test_reopening_the_application_preserves_imported_data(tmp_path: Path) -> None:
    """Simulates "stop the process, start it again" — a second `create_app()`
    against the same database file must see everything the first one wrote.
    """
    from threnda.app.db.base import Base
    from threnda.app.db.session import create_engine_from_settings

    db_path = tmp_path / "reopen-test.db"
    settings = Settings(database_url=f"sqlite:///{db_path}", secret_key="test-secret")  # noqa: S106
    engine = create_engine_from_settings(settings)
    Base.metadata.create_all(engine)
    engine.dispose()

    content = (DEMO_DATA / "json" / "synthetic-findings.json").read_bytes()

    first_app = create_app(settings=settings)
    with TestClient(first_app) as first_client:
        session = first_app.state.sessionmaker()
        workspace = Workspace(name="synthetic-reopen")
        session.add(workspace)
        session.flush()
        source = Source(workspace_id=workspace.id, name="s", source_type=SourceType.JSON)
        session.add(source)
        session.commit()
        workspace_id, source_id = workspace.id, source.id
        session.close()

        response = first_client.post(
            "/api/v1/imports",
            params={"workspace_id": workspace_id},
            data={"source_id": str(source_id)},
            files={"file": ("f.json", content, "application/json")},
        )
        assert response.status_code == 201, response.text

    with TestClient(create_app(settings=settings)) as second_client:
        listing = second_client.get("/api/v1/imports", params={"workspace_id": workspace_id}).json()
        assert listing["total"] == 1
        assert listing["items"][0]["successful_records"] == 3

        assets = second_client.get("/api/v1/assets", params={"workspace_id": workspace_id}).json()
        assert assets["total"] == 3
