"""Independent regressions for Milestone 3 differential safety."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.core.config import Settings
from threnda.app.main import create_app
from threnda.app.models import ExposureHistory, Finding, ImportRun, Source, Workspace
from threnda.app.models.enums import ExposureHistoryEventType, SeverityLevel, SourceType
from threnda.app.services.ingestion.base import NormalizedFinding, ParsedImport, RowError
from threnda.app.services.ingestion.json_importer import JsonImporter
from threnda.app.services.ingestion.runner import persist_parsed_import


def make_source(db: Session, name: str = "Synthetic review") -> tuple[int, int]:
    workspace = Workspace(name=name)
    db.add(workspace)
    db.flush()
    source = Source(workspace_id=workspace.id, name=name, source_type=SourceType.JSON)
    db.add(source)
    db.commit()
    return workspace.id, source.id


def normalized(identity: str, title: str) -> NormalizedFinding:
    return NormalizedFinding(
        source_finding_id=identity,
        fingerprint=f"fingerprint-{identity}",
        title=title,
        severity=SeverityLevel.LOW,
    )


def test_json_stable_id_title_change_is_updated_not_new(db_session: Session) -> None:
    workspace_id, source_id = make_source(db_session, "Synthetic stable ID")
    importer = JsonImporter()
    first_raw = json.dumps(
        {"findings": [{"id": "stable-1", "title": "Original title", "severity": "low"}]}
    ).encode()
    second_raw = json.dumps(
        {"findings": [{"id": "stable-1", "title": "Renamed title", "severity": "low"}]}
    ).encode()
    persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="first.json",
        raw=first_raw,
        parsed=importer.parse(first_raw),
    )
    second = persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="second.json",
        raw=second_raw,
        parsed=importer.parse(second_raw),
    )
    rows = db_session.scalars(select(Finding).where(Finding.source_id == source_id)).all()
    assert len(rows) == 1
    assert rows[0].title == "Renamed title"
    assert (second.new_findings, second.updated_findings, second.resolved_findings) == (0, 1, 0)


def test_partial_row_error_does_not_resolve_unseen_finding(db_session: Session) -> None:
    workspace_id, source_id = make_source(db_session, "Synthetic partial error")
    first = ParsedImport(
        findings=(normalized("a", "Synthetic A"), normalized("b", "Synthetic B")),
        errors=(),
        total_records=2,
    )
    persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="complete.json",
        raw=b"complete",
        parsed=first,
    )
    partial = ParsedImport(
        findings=(normalized("a", "Synthetic A"),),
        errors=(RowError(index=1, message="synthetic invalid record"),),
        total_records=2,
    )
    second = persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="partial.json",
        raw=b"partial",
        parsed=partial,
    )
    rows = db_session.scalars(select(Finding).where(Finding.source_id == source_id)).all()
    assert second.failed_records == 1
    assert second.resolved_findings == 0
    assert all(row.resolved_at is None for row in rows)


def test_resolve_and_reopen_are_traceable_to_the_finding(db_session: Session) -> None:
    workspace_id, source_id = make_source(db_session, "Synthetic lifecycle history")
    both = ParsedImport(
        findings=(normalized("a", "Synthetic A"), normalized("b", "Synthetic B")),
        errors=(),
        total_records=2,
    )
    only_a = ParsedImport(findings=(normalized("a", "Synthetic A"),), errors=(), total_records=1)
    persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="both.json",
        raw=b"both",
        parsed=both,
    )
    target = db_session.scalars(
        select(Finding).where(Finding.source_id == source_id, Finding.source_finding_id == "b")
    ).one()
    persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="only-a.json",
        raw=b"only-a",
        parsed=only_a,
    )
    persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="both-again.json",
        raw=b"both-again",
        parsed=both,
    )
    events = db_session.scalars(
        select(ExposureHistory).where(ExposureHistory.exposure_id == target.exposure_id)
    ).all()
    event_types = {event.event_type for event in events}
    assert ExposureHistoryEventType.RESOLVED in event_types
    assert ExposureHistoryEventType.REOPENED in event_types
    lifecycle_events = [
        event
        for event in events
        if event.event_type
        in {ExposureHistoryEventType.RESOLVED, ExposureHistoryEventType.REOPENED}
    ]
    assert all(f"finding_id={target.id}" in (event.new_value or "") for event in lifecycle_events)


def test_duplicate_normalized_identity_is_a_durable_client_error(
    initialized_settings: Settings,
) -> None:
    from threnda.app.services.user_accounts import create_user

    app = create_app(settings=initialized_settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        session = app.state.sessionmaker()
        create_user(session, "reviewer", "Reviewer-Pass-1234!", actor=None)
        session.commit()
        session.close()
        login = client.post(
            "/api/v1/auth/login", json={"username": "reviewer", "password": "Reviewer-Pass-1234!"}
        )
        assert login.status_code == 200, login.text

        workspace = client.post("/api/v1/workspaces", json={"name": "Synthetic duplicate"}).json()
        source = client.post(
            "/api/v1/sources",
            params={"workspace_id": workspace["id"]},
            json={"name": "Synthetic duplicate", "source_type": "json"},
        ).json()
        payload = {
            "findings": [
                {"id": "same", "title": "Synthetic duplicate", "severity": "low"},
                {"id": "same", "title": "Synthetic duplicate", "severity": "low"},
            ]
        }
        response = client.post(
            "/api/v1/imports",
            params={"workspace_id": workspace["id"]},
            data={"source_id": str(source["id"])},
            files={"file": ("duplicates.json", json.dumps(payload), "application/json")},
        )
        assert response.status_code == 422
        body = response.json()
        assert body["import_run"]["status"] == "failed"

    with app.state.sessionmaker() as db:
        runs = db.scalars(select(ImportRun).where(ImportRun.source_id == source["id"])).all()
        findings = db.scalars(select(Finding).where(Finding.source_id == source["id"])).all()
        assert len(runs) == 1
        assert findings == []
