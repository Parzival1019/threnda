"""Unit tests for the extracted ``handle_upload`` ingestion service.

Complements ``tests/test_api_imports_upload.py`` (the real HTTP contract);
these exercise the shared detect->parse->persist sequence directly, since
Milestone 5's web upload route calls it without going through that endpoint.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from threnda.app.models.enums import ImportStatus, SourceType
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace
from threnda.app.services.ingestion.upload import (
    MissingCsvMappingError,
    UnsupportedSourceTypeError,
    handle_upload,
    sanitize_filename,
)

CSV_MAPPING = {
    "Vulnerability": "title",
    "Host": "asset",
    "Severity": "severity",
}

DEMO_DATA = Path(__file__).resolve().parents[1] / "demo-data"


def _make_workspace_and_source(db: Session, source_type: SourceType) -> tuple[Workspace, Source]:
    workspace = Workspace(name=f"synthetic-upload-{source_type.value}")
    db.add(workspace)
    db.flush()
    source = Source(workspace_id=workspace.id, name="synthetic-source", source_type=source_type)
    db.add(source)
    db.flush()
    return workspace, source


def test_handle_upload_json_happy_path(db_session: Session) -> None:
    workspace, source = _make_workspace_and_source(db_session, SourceType.JSON)
    raw = json.dumps(
        {"findings": [{"id": "f1", "title": "Synthetic finding", "severity": "high"}]}
    ).encode()

    outcome = handle_upload(
        db_session,
        workspace_id=workspace.id,
        source=source,
        filename="findings.json",
        content_type="application/json",
        raw=raw,
        csv_mapping=None,
    )

    assert not outcome.failed
    assert outcome.import_run.status == ImportStatus.COMPLETED
    assert outcome.import_run.successful_records == 1
    assert outcome.row_errors == []


def test_handle_upload_malformed_json_returns_failed_outcome(db_session: Session) -> None:
    workspace, source = _make_workspace_and_source(db_session, SourceType.JSON)

    outcome = handle_upload(
        db_session,
        workspace_id=workspace.id,
        source=source,
        filename="broken.json",
        content_type="application/json",
        raw=b"{not valid json",
        csv_mapping=None,
    )

    assert outcome.failed
    assert outcome.import_run.status == ImportStatus.FAILED
    assert outcome.failure_detail is not None


def test_handle_upload_csv_without_mapping_raises(db_session: Session) -> None:
    workspace, source = _make_workspace_and_source(db_session, SourceType.CSV)

    with pytest.raises(MissingCsvMappingError):
        handle_upload(
            db_session,
            workspace_id=workspace.id,
            source=source,
            filename="report.csv",
            content_type="text/csv",
            raw=b"Vulnerability,Host,Severity\nSynthetic issue,host.example.test,high\n",
            csv_mapping=None,
        )


def test_handle_upload_auto_detects_scanner_csv_and_saves_profile(db_session: Session) -> None:
    workspace, source = _make_workspace_and_source(db_session, SourceType.CSV)
    raw = (DEMO_DATA / "csv" / "scanners" / "qualys-vmdr.csv").read_bytes()

    outcome = handle_upload(
        db_session,
        workspace_id=workspace.id,
        source=source,
        filename="qualys.csv",
        content_type="text/csv",
        raw=raw,
        csv_mapping=None,
    )

    assert not outcome.failed
    assert outcome.csv_profile == "Qualys VMDR"
    assert outcome.import_run.successful_records == 1
    assert source.meta["csv_profile"] == "qualys-vmdr"
    assert source.meta["csv_mapping"]["Title"] == "title"


def test_handle_upload_csv_with_mapping_persists_mapping_on_source(db_session: Session) -> None:
    workspace, source = _make_workspace_and_source(db_session, SourceType.CSV)
    raw = b"Vulnerability,Host,Severity\nSynthetic issue,host.example.test,high\n"

    outcome = handle_upload(
        db_session,
        workspace_id=workspace.id,
        source=source,
        filename="report.csv",
        content_type="text/csv",
        raw=raw,
        csv_mapping=CSV_MAPPING,
    )

    assert not outcome.failed
    assert source.meta["csv_mapping"] == CSV_MAPPING


def test_handle_upload_unsupported_source_type_raises(db_session: Session) -> None:
    workspace, source = _make_workspace_and_source(db_session, SourceType.OTHER)

    with pytest.raises(UnsupportedSourceTypeError):
        handle_upload(
            db_session,
            workspace_id=workspace.id,
            source=source,
            filename="report.txt",
            content_type="text/plain",
            raw=b"anything",
            csv_mapping=None,
        )


def test_sanitize_filename_strips_directory_components() -> None:
    assert sanitize_filename("../../../../etc/passwd") == "passwd"
    assert sanitize_filename(None) == "upload"
