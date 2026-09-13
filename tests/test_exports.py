"""CSV/JSON export: schema, formula-injection safety, streaming, auth."""

from __future__ import annotations

import csv
import io
import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from webhelpers import bootstrap_workspace

from threnda.app.models.enums import AuditEventType, SeverityLevel
from threnda.app.models.exposure import Exposure
from threnda.app.models.workspace import Workspace
from threnda.app.services.exports import EXPORT_COLUMNS, _csv_safe


def _seed_exposure(db_session: Session, workspace_id: int, title: str) -> Exposure:
    exposure = Exposure(
        workspace_id=workspace_id,
        canonical_fingerprint=f"fp-{title}",
        title=title,
        severity=SeverityLevel.HIGH,
    )
    db_session.add(exposure)
    db_session.commit()
    return exposure


def test_csv_safe_neutralizes_formula_trigger_characters() -> None:
    for dangerous in ("=cmd()", "+1+1", "-1+1", "@SUM(A1)", "\ttabbed", "\rcr"):
        assert _csv_safe(dangerous).startswith("'")
    assert _csv_safe("a normal title") == "a normal title"
    assert _csv_safe(42) == 42
    assert _csv_safe(None) is None


def test_csv_export_neutralizes_a_formula_injection_title(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    _seed_exposure(db_session, workspace_id, '=HYPERLINK("http://evil.example")')

    response = client.get(
        "/api/v1/exports/exposures", params={"workspace_id": workspace_id, "format": "csv"}
    )
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows[0]["title"].startswith("'=")


def test_csv_export_header_matches_the_documented_schema(
    client: TestClient, db_session: Session
) -> None:
    workspace_id = bootstrap_workspace(client)
    response = client.get(
        "/api/v1/exports/exposures", params={"workspace_id": workspace_id, "format": "csv"}
    )
    header = next(csv.reader(io.StringIO(response.text)))
    assert header == list(EXPORT_COLUMNS)


def test_json_export_is_a_valid_json_array(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    _seed_exposure(db_session, workspace_id, "exposure-one")
    _seed_exposure(db_session, workspace_id, "exposure-two")

    response = client.get(
        "/api/v1/exports/exposures", params={"workspace_id": workspace_id, "format": "json"}
    )
    assert response.status_code == 200
    parsed = json.loads(response.text)
    assert len(parsed) == 2
    assert {row["title"] for row in parsed} == {"exposure-one", "exposure-two"}


def test_export_is_scoped_to_the_workspace(client: TestClient, db_session: Session) -> None:
    workspace_a = bootstrap_workspace(client, "export-a")
    workspace_b = bootstrap_workspace(client, "export-b")
    _seed_exposure(db_session, workspace_a, "only-in-a")
    _seed_exposure(db_session, workspace_b, "only-in-b")

    response = client.get(
        "/api/v1/exports/exposures", params={"workspace_id": workspace_a, "format": "json"}
    )
    titles = {row["title"] for row in json.loads(response.text)}
    assert titles == {"only-in-a"}


def test_export_streams_across_the_batch_boundary(client: TestClient, db_session: Session) -> None:
    workspace_id = bootstrap_workspace(client)
    workspace = db_session.get(Workspace, workspace_id)
    assert workspace is not None
    # 500 is the internal batch size (threnda.app.services.exports) —
    # seed one more than that so the keyset-pagination loop must fetch a
    # second batch.
    for i in range(501):
        db_session.add(
            Exposure(
                workspace_id=workspace_id,
                canonical_fingerprint=f"fp-{i}",
                title=f"exposure-{i}",
                severity=SeverityLevel.LOW,
            )
        )
    db_session.commit()

    response = client.get(
        "/api/v1/exports/exposures", params={"workspace_id": workspace_id, "format": "json"}
    )
    parsed = json.loads(response.text)
    assert len(parsed) == 501
    assert len({row["id"] for row in parsed}) == 501


def test_viewer_cannot_export(client: TestClient, db_session: Session) -> None:
    from threnda.app.models.enums import WorkspaceRole
    from threnda.app.services.memberships import add_member
    from threnda.app.services.user_accounts import create_user

    workspace_id = bootstrap_workspace(client)
    workspace = db_session.get(Workspace, workspace_id)
    assert workspace is not None
    viewer_user = create_user(db_session, "export-viewer", "Viewer-Export-Pass1!", actor=None)
    add_member(db_session, workspace, viewer_user, WorkspaceRole.VIEWER, actor=viewer_user)
    db_session.commit()

    with TestClient(client.app) as viewer_client:
        viewer_client.post(
            "/api/v1/auth/login",
            json={"username": "export-viewer", "password": "Viewer-Export-Pass1!"},
        )
        response = viewer_client.get(
            "/api/v1/exports/exposures", params={"workspace_id": workspace_id}
        )
        assert response.status_code == 403


def test_export_download_is_audited(client: TestClient, db_session: Session) -> None:
    from threnda.app.models.audit_log import AuditLogEntry

    workspace_id = bootstrap_workspace(client)
    client.get("/api/v1/exports/exposures", params={"workspace_id": workspace_id, "format": "csv"})
    entry = (
        db_session.query(AuditLogEntry)
        .filter_by(event_type=AuditEventType.EXPORT_DOWNLOADED, workspace_id=workspace_id)
        .one()
    )
    assert entry.detail == {"format": "csv"}
