"""Tests for the Milestone 3 import differential engine (persist_parsed_import).

Exercises ``persist_parsed_import`` directly with hand-built
``ParsedImport``/``NormalizedFinding`` objects — this is the layer both the
CSV/JSON/SARIF importers and the upload API sit on top of, and testing it
directly (rather than only through a full CSV upload) makes each
NEW/UNCHANGED/UPDATED/RESOLVED/REOPENED transition explicit and easy to
follow.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.asset import Asset
from threnda.app.models.enums import SeverityLevel, SourceType
from threnda.app.models.finding import Finding
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace
from threnda.app.services.ingestion.base import (
    DuplicateIdentityError,
    NormalizedAsset,
    NormalizedFinding,
    ParsedImport,
)
from threnda.app.services.ingestion.runner import (
    find_duplicate_source_finding_ids,
    persist_parsed_import,
)


def _workspace_and_source(db: Session) -> tuple[int, int]:
    workspace = Workspace(name="synthetic-differential")
    db.add(workspace)
    db.flush()
    source = Source(workspace_id=workspace.id, name="synthetic-source", source_type=SourceType.JSON)
    db.add(source)
    db.commit()
    return workspace.id, source.id


def _finding(
    *,
    fingerprint: str,
    title: str,
    severity: SeverityLevel = SeverityLevel.MEDIUM,
    asset_key: str | None = None,
) -> NormalizedFinding:
    asset = (
        NormalizedAsset(canonical_key=asset_key, name=asset_key) if asset_key is not None else None
    )
    return NormalizedFinding(
        source_finding_id=fingerprint,
        fingerprint=fingerprint,
        title=title,
        severity=severity,
        asset=asset,
    )


def _parsed(*findings: NormalizedFinding) -> ParsedImport:
    return ParsedImport(findings=tuple(findings), errors=(), total_records=len(findings))


def test_first_import_is_all_new_and_correlates_each_finding(db_session: Session) -> None:
    workspace_id, source_id = _workspace_and_source(db_session)
    parsed = _parsed(
        _finding(fingerprint="fp-1", title="Finding one"),
        _finding(fingerprint="fp-2", title="Finding two"),
    )

    run = persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="f.json",
        raw=b"{}",
        parsed=parsed,
    )

    assert (run.new_findings, run.unchanged_findings, run.updated_findings) == (2, 0, 0)
    assert (run.resolved_findings, run.reopened_findings) == (0, 0)
    assert run.successful_records == 2

    findings = db_session.scalars(select(Finding).where(Finding.workspace_id == workspace_id)).all()
    assert len(findings) == 2
    assert all(f.exposure_id is not None for f in findings)
    assert all(f.resolved_at is None for f in findings)


def test_reimporting_identical_data_is_all_unchanged_no_duplicates(db_session: Session) -> None:
    workspace_id, source_id = _workspace_and_source(db_session)
    parsed = _parsed(_finding(fingerprint="fp-1", title="Finding one"))
    persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="f1.json",
        raw=b"1",
        parsed=parsed,
    )

    run2 = persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="f2.json",
        raw=b"2",
        parsed=parsed,
    )

    assert (run2.new_findings, run2.unchanged_findings) == (0, 1)
    findings = db_session.scalars(select(Finding).where(Finding.workspace_id == workspace_id)).all()
    assert len(findings) == 1, "must not create a duplicate row for an unchanged finding"


def test_changed_content_is_updated_in_place(db_session: Session) -> None:
    workspace_id, source_id = _workspace_and_source(db_session)
    persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="f1.json",
        raw=b"1",
        parsed=_parsed(
            _finding(fingerprint="fp-1", title="Finding one", severity=SeverityLevel.MEDIUM)
        ),
    )

    run2 = persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="f2.json",
        raw=b"2",
        parsed=_parsed(
            _finding(fingerprint="fp-1", title="Finding one", severity=SeverityLevel.CRITICAL)
        ),
    )

    assert (run2.new_findings, run2.unchanged_findings, run2.updated_findings) == (0, 0, 1)
    finding = db_session.scalars(
        select(Finding).where(Finding.workspace_id == workspace_id, Finding.fingerprint == "fp-1")
    ).one()
    assert finding.severity is SeverityLevel.CRITICAL


def test_a_finding_missing_from_a_later_import_is_resolved_not_deleted(db_session: Session) -> None:
    workspace_id, source_id = _workspace_and_source(db_session)
    persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="f1.json",
        raw=b"1",
        parsed=_parsed(
            _finding(fingerprint="fp-1", title="Finding one"),
            _finding(fingerprint="fp-2", title="Finding two"),
        ),
    )

    run2 = persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="f2.json",
        raw=b"2",
        parsed=_parsed(_finding(fingerprint="fp-1", title="Finding one")),
    )

    assert run2.resolved_findings == 1
    assert run2.unchanged_findings == 1
    all_findings = db_session.scalars(
        select(Finding).where(Finding.workspace_id == workspace_id)
    ).all()
    assert len(all_findings) == 2, "resolved findings are never deleted"
    resolved = db_session.scalars(
        select(Finding).where(Finding.workspace_id == workspace_id, Finding.fingerprint == "fp-2")
    ).one()
    assert resolved.resolved_at is not None


def test_a_resolved_finding_reappearing_is_reopened(db_session: Session) -> None:
    workspace_id, source_id = _workspace_and_source(db_session)
    both = _parsed(
        _finding(fingerprint="fp-1", title="Finding one"),
        _finding(fingerprint="fp-2", title="Finding two"),
    )
    only_one = _parsed(_finding(fingerprint="fp-1", title="Finding one"))

    persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="f1.json",
        raw=b"1",
        parsed=both,
    )
    persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="f2.json",
        raw=b"2",
        parsed=only_one,
    )
    run3 = persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="f3.json",
        raw=b"3",
        parsed=both,
    )

    assert run3.reopened_findings == 1
    assert run3.new_findings == 0
    reopened = db_session.scalars(
        select(Finding).where(Finding.workspace_id == workspace_id, Finding.fingerprint == "fp-2")
    ).one()
    assert reopened.resolved_at is None
    assert reopened.exposure_id is not None, "a reopened finding keeps its original exposure link"


def test_matching_asset_key_is_reused_not_duplicated_across_imports(db_session: Session) -> None:
    workspace_id, source_id = _workspace_and_source(db_session)
    parsed = _parsed(
        _finding(fingerprint="fp-1", title="Finding one", asset_key="host:shared.example.test")
    )

    persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="f1.json",
        raw=b"1",
        parsed=parsed,
    )
    persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="f2.json",
        raw=b"2",
        parsed=parsed,
    )

    assets = db_session.scalars(select(Asset).where(Asset.workspace_id == workspace_id)).all()
    assert len(assets) == 1


def test_row_errors_are_counted_as_failed_records_not_dropped_silently(db_session: Session) -> None:
    from threnda.app.services.ingestion.base import RowError

    workspace_id, source_id = _workspace_and_source(db_session)
    parsed = ParsedImport(
        findings=(_finding(fingerprint="fp-1", title="Good"),),
        errors=(RowError(index=1, message="bad row"),),
        total_records=2,
    )

    run = persist_parsed_import(
        db_session,
        workspace_id=workspace_id,
        source_id=source_id,
        filename="f.json",
        raw=b"1",
        parsed=parsed,
    )

    assert run.failed_records == 1
    assert run.successful_records == 1
    assert run.total_records == 2


def test_find_duplicate_source_finding_ids_reports_each_colliding_value_once() -> None:
    findings = (
        _finding(fingerprint="a", title="A"),
        _finding(fingerprint="b", title="B"),
        _finding(fingerprint="a", title="A again"),
        _finding(fingerprint="a", title="A a third time"),
    )

    assert find_duplicate_source_finding_ids(findings) == ["a"]


def test_find_duplicate_source_finding_ids_is_empty_when_all_unique() -> None:
    findings = (_finding(fingerprint="a", title="A"), _finding(fingerprint="b", title="B"))

    assert find_duplicate_source_finding_ids(findings) == []


def test_duplicate_identity_in_one_import_writes_nothing(db_session: Session) -> None:
    """The runner-level guard behind the HTTP-level regression in
    tests/test_independent_milestone3_review.py: raised before the
    ImportRun itself is created, so a caller that doesn't go through the
    upload API (as this test doesn't) sees the same "nothing written"
    guarantee."""
    workspace_id, source_id = _workspace_and_source(db_session)
    parsed = _parsed(
        _finding(fingerprint="dup", title="First copy"),
        _finding(fingerprint="dup", title="Second copy"),
    )

    with pytest.raises(DuplicateIdentityError):
        persist_parsed_import(
            db_session,
            workspace_id=workspace_id,
            source_id=source_id,
            filename="f.json",
            raw=b"1",
            parsed=parsed,
        )

    findings = db_session.scalars(select(Finding).where(Finding.source_id == source_id)).all()
    assert findings == []
    from threnda.app.models.import_run import ImportRun

    runs = db_session.scalars(select(ImportRun).where(ImportRun.source_id == source_id)).all()
    assert runs == []
