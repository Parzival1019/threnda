"""CSV source-local identity must survive row reordering.

docs/milestone-3-review.md asked specifically for CSV reorder to be thought
through: once the differential engine matches on ``source_finding_id``
rather than ``fingerprint`` (see tests/test_independent_milestone3_review.py
for the title-change bug that motivated that switch), a *positional*
identity like the old ``row-{index}`` fallback would make a CSV re-exported
with the same rows in a different order look like every row resolved and a
new one appeared. The fixed fallback (csv_importer.py) derives identity from
content instead of position, so reordering must not disturb it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.enums import SourceType
from threnda.app.models.finding import Finding
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace
from threnda.app.services.ingestion.csv_importer import CsvImporter
from threnda.app.services.ingestion.runner import persist_parsed_import

MAPPING = {
    "Vulnerability": "title",
    "Host": "asset",
    "Severity": "severity",
    "CVE": "cve",
}

ORIGINAL_ORDER = (
    b"Vulnerability,Host,Severity,CVE\r\n"
    b"Outdated TLS,host-a.example.test,High,CVE-2099-1\r\n"
    b"Default credentials,host-b.example.test,Critical,CVE-2099-2\r\n"
    b"Verbose errors,host-c.example.test,Medium,\r\n"
)

REORDERED = (
    b"Vulnerability,Host,Severity,CVE\r\n"
    b"Verbose errors,host-c.example.test,Medium,\r\n"
    b"Outdated TLS,host-a.example.test,High,CVE-2099-1\r\n"
    b"Default credentials,host-b.example.test,Critical,CVE-2099-2\r\n"
)


def _workspace_and_source(db: Session) -> tuple[int, int]:
    workspace = Workspace(name="synthetic-csv-reorder")
    db.add(workspace)
    db.flush()
    source = Source(workspace_id=workspace.id, name="synthetic-csv", source_type=SourceType.CSV)
    db.add(source)
    db.commit()
    return workspace.id, source.id


def test_reordered_but_unchanged_csv_rows_are_all_unchanged(db_session: Session) -> None:
    workspace_id, source_id = _workspace_and_source(db_session)
    importer = CsvImporter(mapping=MAPPING)

    first = persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="first.csv",
        raw=ORIGINAL_ORDER,
        parsed=importer.parse(ORIGINAL_ORDER),
    )
    assert (first.new_findings, first.unchanged_findings) == (3, 0)

    second = persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="second.csv",
        raw=REORDERED,
        parsed=importer.parse(REORDERED),
    )

    assert (second.new_findings, second.unchanged_findings, second.updated_findings) == (0, 3, 0)
    assert second.resolved_findings == 0
    findings = db_session.scalars(select(Finding).where(Finding.source_id == source_id)).all()
    assert len(findings) == 3, "reordering must never create duplicate rows for the same content"


def test_a_genuinely_edited_csv_row_is_resolved_and_new_not_updated(db_session: Session) -> None:
    """Documented, honest limitation: CSV has no external id, so its
    identity *is* the content signature — editing a row's title is
    indistinguishable from "the old one disappeared, a new one appeared".
    This is the CSV-specific trade-off docs/deduplication.md documents,
    verified here so a future change doesn't silently alter it without
    updating that documentation.
    """
    workspace_id, source_id = _workspace_and_source(db_session)
    importer = CsvImporter(mapping=MAPPING)
    persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="first.csv",
        raw=ORIGINAL_ORDER,
        parsed=importer.parse(ORIGINAL_ORDER),
    )

    edited = ORIGINAL_ORDER.replace(b"Outdated TLS", b"Outdated TLS configuration")
    second = persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="second.csv",
        raw=edited,
        parsed=importer.parse(edited),
    )

    assert second.new_findings == 1
    assert second.resolved_findings == 1
    assert second.unchanged_findings == 2
