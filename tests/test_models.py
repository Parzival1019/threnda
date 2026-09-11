"""Model, integrity and persistence tests.

All fixture data below is synthetic (invented hostnames, CVEs and titles) —
see CONTRIBUTING.md. Nothing here originates from a real scan or report.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from threnda.app.models.asset import Asset
from threnda.app.models.enums import (
    AssetType,
    ExposureHistoryEventType,
    ExposureStatus,
    SeverityLevel,
    SourceType,
)
from threnda.app.models.exposure import Exposure
from threnda.app.models.exposure_history import ExposureHistory
from threnda.app.models.finding import Finding
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace


def _make_workspace(db_session: Session, name: str = "default") -> Workspace:
    workspace = Workspace(name=name)
    db_session.add(workspace)
    db_session.flush()
    return workspace


def _make_source(db_session: Session, workspace: Workspace) -> Source:
    source = Source(
        workspace_id=workspace.id,
        name="Synthetic CSV export",
        source_type=SourceType.CSV,
        meta={"tool": "synthetic-scanner", "version": "1.0"},
    )
    db_session.add(source)
    db_session.flush()
    return source


def test_workspace_created_at_is_utc_aware(db_session: Session) -> None:
    workspace = _make_workspace(db_session)
    assert workspace.created_at.tzinfo is not None
    assert workspace.created_at.utcoffset() == timedelta(0)
    assert workspace.created_at <= datetime.now(UTC)


def test_source_metadata_column_roundtrips_via_meta_attribute(db_session: Session) -> None:
    workspace = _make_workspace(db_session)
    source = _make_source(db_session, workspace)
    db_session.commit()

    # The Python attribute is `meta`, but the underlying column is
    # literally named "metadata" (see threnda.app.models.source).
    raw = db_session.execute(
        text("SELECT metadata FROM sources WHERE id = :id"), {"id": source.id}
    ).scalar_one()
    assert '"tool": "synthetic-scanner"' in raw


def test_enum_values_are_stored_as_lowercase_strings(db_session: Session) -> None:
    workspace = _make_workspace(db_session)
    source = _make_source(db_session, workspace)
    finding = Finding(
        workspace_id=workspace.id,
        fingerprint="fp-1",
        source_id=source.id,
        source_finding_id="row-1",
        title="Outdated TLS configuration on synthetic-host-01.example.test",
        severity=SeverityLevel.CRITICAL,
    )
    db_session.add(finding)
    db_session.commit()

    raw_severity = db_session.execute(
        text("SELECT severity FROM findings WHERE id = :id"), {"id": finding.id}
    ).scalar_one()
    assert raw_severity == "critical"


def test_full_domain_graph_persists_and_relates(db_session: Session) -> None:
    workspace = _make_workspace(db_session)
    source = _make_source(db_session, workspace)

    asset = Asset(
        workspace_id=workspace.id,
        canonical_key="host:synthetic-host-01.example.test",
        name="synthetic-host-01.example.test",
        asset_type=AssetType.HOST,
    )
    db_session.add(asset)
    db_session.flush()

    exposure = Exposure(
        workspace_id=workspace.id,
        canonical_fingerprint="exp-fp-1",
        title="Outdated TLS configuration",
        severity=SeverityLevel.HIGH,
        risk_score=72,
    )
    db_session.add(exposure)
    db_session.flush()

    finding = Finding(
        workspace_id=workspace.id,
        fingerprint="fp-2",
        source_id=source.id,
        asset_id=asset.id,
        exposure_id=exposure.id,
        source_finding_id="row-2",
        title="Outdated TLS configuration",
        severity=SeverityLevel.HIGH,
        cve="CVE-2099-00001",
        references=["https://example.test/advisories/2099-00001"],
        raw_data={"raw_severity": "High"},
    )
    db_session.add(finding)
    db_session.commit()

    db_session.refresh(exposure)
    assert len(exposure.findings) == 1
    assert exposure.findings[0].id == finding.id
    assert finding.asset is not None
    assert finding.asset.canonical_key == "host:synthetic-host-01.example.test"
    assert finding.references == ["https://example.test/advisories/2099-00001"]


def test_asset_canonical_key_is_unique_per_workspace(db_session: Session) -> None:
    workspace = _make_workspace(db_session)
    db_session.add(
        Asset(workspace_id=workspace.id, canonical_key="host:dup.example.test", name="dup")
    )
    db_session.flush()

    db_session.add(
        Asset(workspace_id=workspace.id, canonical_key="host:dup.example.test", name="dup-2")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_asset_canonical_key_can_repeat_across_workspaces(db_session: Session) -> None:
    workspace_a = _make_workspace(db_session, name="workspace-a")
    workspace_b = _make_workspace(db_session, name="workspace-b")

    db_session.add(
        Asset(workspace_id=workspace_a.id, canonical_key="host:shared.example.test", name="a")
    )
    db_session.add(
        Asset(workspace_id=workspace_b.id, canonical_key="host:shared.example.test", name="b")
    )
    db_session.commit()  # must not raise


def test_foreign_keys_are_enforced(db_session: Session) -> None:
    """SQLite must reject an orphaned foreign key (PRAGMA foreign_keys=ON)."""
    db_session.add(
        Finding(
            workspace_id=999_999,
            fingerprint="fp-orphan",
            source_id=999_999,
            source_finding_id="row-orphan",
            title="Should never persist",
            severity=SeverityLevel.LOW,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_exposure_risk_score_check_constraint(db_session: Session) -> None:
    workspace = _make_workspace(db_session)
    db_session.add(
        Exposure(
            workspace_id=workspace.id,
            canonical_fingerprint="exp-fp-invalid",
            title="Invalid score",
            severity=SeverityLevel.LOW,
            risk_score=150,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_deleting_workspace_cascades_to_children(db_session: Session) -> None:
    workspace = _make_workspace(db_session)
    _make_source(db_session, workspace)
    db_session.commit()

    workspace_id = workspace.id
    db_session.delete(workspace)
    db_session.commit()

    remaining_sources = (
        db_session.execute(select(Source).where(Source.workspace_id == workspace_id))
        .scalars()
        .all()
    )
    assert remaining_sources == []


def test_exposure_history_records_audit_trail(db_session: Session) -> None:
    workspace = _make_workspace(db_session)
    exposure = Exposure(
        workspace_id=workspace.id,
        canonical_fingerprint="exp-fp-history",
        title="Weak password policy",
        severity=SeverityLevel.MEDIUM,
    )
    db_session.add(exposure)
    db_session.flush()

    db_session.add(
        ExposureHistory(
            exposure_id=exposure.id,
            event_type=ExposureHistoryEventType.STATUS_CHANGED,
            previous_value=ExposureStatus.OPEN.value,
            new_value=ExposureStatus.ACKNOWLEDGED.value,
            actor="unit-test",
        )
    )
    db_session.commit()
    db_session.refresh(exposure)

    assert len(exposure.history) == 1
    assert exposure.history[0].event_type is ExposureHistoryEventType.STATUS_CHANGED
    assert exposure.history[0].timestamp.tzinfo is not None


def test_exposure_remediation_target_date_is_a_plain_date(db_session: Session) -> None:
    workspace = _make_workspace(db_session)
    target = date(2099, 1, 1)
    exposure = Exposure(
        workspace_id=workspace.id,
        canonical_fingerprint="exp-fp-date",
        title="Needs a deadline",
        severity=SeverityLevel.LOW,
        remediation_target_date=target,
    )
    db_session.add(exposure)
    db_session.commit()
    db_session.refresh(exposure)

    assert exposure.remediation_target_date == target
