"""Tests for the correlation engine: EXACT / HIGH / POSSIBLE / no match.

Synthetic fixtures only. Similarity scores used as thresholds below were
verified directly against ``title_similarity`` before being written here
(see the docstrings) rather than picked to happen to work.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.enums import (
    ExposureHistoryEventType,
    PossibleDuplicateStatus,
    SeverityLevel,
)
from threnda.app.models.exposure import Exposure
from threnda.app.models.exposure_history import ExposureHistory
from threnda.app.models.finding import Finding
from threnda.app.models.possible_duplicate import PossibleDuplicate
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace
from threnda.app.services.dedup.correlation import correlate_new_finding


def _make_workspace_and_source(db: Session) -> tuple[Workspace, Source]:
    from threnda.app.models.enums import SourceType

    workspace = Workspace(name="synthetic-dedup")
    db.add(workspace)
    db.flush()
    source = Source(workspace_id=workspace.id, name="synthetic-source", source_type=SourceType.JSON)
    db.add(source)
    db.flush()
    return workspace, source


def _make_existing_finding_and_exposure(
    db: Session,
    workspace: Workspace,
    source: Source,
    *,
    fingerprint: str,
    title: str,
    cve: str | None = None,
) -> tuple[Finding, Exposure]:
    exposure = Exposure(
        workspace_id=workspace.id,
        canonical_fingerprint=fingerprint,
        title=title,
        severity=SeverityLevel.HIGH,
    )
    db.add(exposure)
    db.flush()
    finding = Finding(
        workspace_id=workspace.id,
        source_id=source.id,
        fingerprint=fingerprint,
        source_finding_id="existing",
        title=title,
        severity=SeverityLevel.HIGH,
        cve=cve,
        exposure_id=exposure.id,
    )
    db.add(finding)
    db.commit()
    return finding, exposure


def _make_new_finding(
    db: Session,
    workspace: Workspace,
    source: Source,
    *,
    fingerprint: str,
    title: str,
    cve: str | None = None,
) -> Finding:
    finding = Finding(
        workspace_id=workspace.id,
        source_id=source.id,
        fingerprint=fingerprint,
        source_finding_id="new",
        title=title,
        severity=SeverityLevel.HIGH,
        cve=cve,
    )
    db.add(finding)
    db.flush()
    return finding


def test_no_signal_at_all_creates_standalone_exposure(db_session: Session) -> None:
    workspace, source = _make_workspace_and_source(db_session)
    finding = _make_new_finding(db_session, workspace, source, fingerprint="fp-a", title="Anything")

    correlate_new_finding(db_session, finding)

    assert finding.exposure_id is not None
    exposure = db_session.get(Exposure, finding.exposure_id)
    assert exposure is not None
    assert exposure.title == "Anything"
    assert db_session.scalars(select(PossibleDuplicate)).all() == []


def test_exact_fingerprint_match_across_sources_autocorrelates(db_session: Session) -> None:
    """Same fingerprint from a *different* source — the same fingerprint
    from the *same* source is a differential-engine "unchanged" match
    (see tests/test_differential_engine.py), not a correlation decision at
    all, and would violate uq_findings_workspace_source_fingerprint besides.
    """
    from threnda.app.models.enums import SourceType

    workspace, source_a = _make_workspace_and_source(db_session)
    source_b = Source(
        workspace_id=workspace.id, name="synthetic-source-b", source_type=SourceType.SARIF
    )
    db_session.add(source_b)
    db_session.flush()

    _existing, exposure = _make_existing_finding_and_exposure(
        db_session,
        workspace,
        source_a,
        fingerprint="shared-fp",
        title="Same issue, same fingerprint",
    )
    new_finding = _make_new_finding(
        db_session,
        workspace,
        source_b,
        fingerprint="shared-fp",
        title="Same issue, same fingerprint",
    )

    correlate_new_finding(db_session, new_finding, actor="tester")

    assert new_finding.exposure_id == exposure.id
    history = db_session.scalars(
        select(ExposureHistory).where(ExposureHistory.exposure_id == exposure.id)
    ).all()
    assert len(history) == 1
    assert history[0].event_type is ExposureHistoryEventType.FINDING_CORRELATED
    assert "confidence=exact" in (history[0].new_value or "")
    assert history[0].actor == "tester"


def test_high_similarity_with_shared_cve_autocorrelates(db_session: Session) -> None:
    """0.978 similarity, verified directly against title_similarity()."""
    workspace, source = _make_workspace_and_source(db_session)
    _existing, exposure = _make_existing_finding_and_exposure(
        db_session,
        workspace,
        source,
        fingerprint="fp-1",
        title="Outdated TLS configuration on synthetic-web-01",
        cve="CVE-2099-00001",
    )
    new_finding = _make_new_finding(
        db_session,
        workspace,
        source,
        fingerprint="fp-2",
        title="Outdated TLS configuration on synthetic-web-02",
        cve="CVE-2099-00001",
    )

    correlate_new_finding(db_session, new_finding)

    assert new_finding.exposure_id == exposure.id
    history = db_session.scalars(
        select(ExposureHistory).where(ExposureHistory.exposure_id == exposure.id)
    ).one()
    assert "confidence=high" in (history.new_value or "")
    assert db_session.scalars(select(PossibleDuplicate)).all() == []


def test_possible_similarity_creates_review_candidate_and_never_auto_merges(
    db_session: Session,
) -> None:
    """0.750 similarity, verified directly against title_similarity() — in
    the POSSIBLE band (>=0.72, <0.92)."""
    workspace, source = _make_workspace_and_source(db_session)
    _existing, exposure = _make_existing_finding_and_exposure(
        db_session,
        workspace,
        source,
        fingerprint="fp-1",
        title="SQL injection in login form",
        cve="CVE-2099-00002",
    )
    new_finding = _make_new_finding(
        db_session,
        workspace,
        source,
        fingerprint="fp-2",
        title="SQL injection vulnerability in the login form",
        cve="CVE-2099-00002",
    )

    correlate_new_finding(db_session, new_finding)

    # Never auto-linked, regardless of confidence score.
    assert new_finding.exposure_id is None
    candidates = db_session.scalars(select(PossibleDuplicate)).all()
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.finding_id == new_finding.id
    assert candidate.candidate_exposure_id == exposure.id
    assert candidate.status is PossibleDuplicateStatus.PENDING
    assert 0.72 <= candidate.similarity_score < 0.92


def test_low_similarity_with_shared_cve_creates_standalone_exposure(db_session: Session) -> None:
    """0.302 similarity — sharing a CVE alone is not enough without the text
    actually looking alike."""
    workspace, source = _make_workspace_and_source(db_session)
    _existing, exposure = _make_existing_finding_and_exposure(
        db_session,
        workspace,
        source,
        fingerprint="fp-1",
        title="Outdated TLS configuration",
        cve="CVE-2099-00003",
    )
    new_finding = _make_new_finding(
        db_session,
        workspace,
        source,
        fingerprint="fp-2",
        title="Something totally different",
        cve="CVE-2099-00003",
    )

    correlate_new_finding(db_session, new_finding)

    assert new_finding.exposure_id is not None
    assert new_finding.exposure_id != exposure.id
    assert db_session.scalars(select(PossibleDuplicate)).all() == []


def test_high_similarity_without_a_shared_signal_is_never_compared(db_session: Session) -> None:
    """Same 0.978-similarity titles as the HIGH test above, but with no
    shared CVE or asset between the two findings — the candidate search
    must not even consider the existing exposure, so nothing correlates
    despite the text being almost identical.
    """
    workspace, source = _make_workspace_and_source(db_session)
    _existing, exposure = _make_existing_finding_and_exposure(
        db_session,
        workspace,
        source,
        fingerprint="fp-1",
        title="Outdated TLS configuration on synthetic-web-01",
        cve=None,
    )
    new_finding = _make_new_finding(
        db_session,
        workspace,
        source,
        fingerprint="fp-2",
        title="Outdated TLS configuration on synthetic-web-02",
        cve=None,
    )

    correlate_new_finding(db_session, new_finding)

    assert new_finding.exposure_id is not None
    assert new_finding.exposure_id != exposure.id
    assert db_session.scalars(select(PossibleDuplicate)).all() == []


def test_shared_asset_is_also_a_valid_gating_signal(db_session: Session) -> None:
    from threnda.app.models.asset import Asset

    workspace, source = _make_workspace_and_source(db_session)
    asset = Asset(
        workspace_id=workspace.id, canonical_key="host:shared.example.test", name="shared"
    )
    db_session.add(asset)
    db_session.flush()

    exposure = Exposure(
        workspace_id=workspace.id,
        canonical_fingerprint="fp-1",
        title="Outdated TLS configuration on synthetic-web-01",
        severity=SeverityLevel.HIGH,
    )
    db_session.add(exposure)
    db_session.flush()
    existing = Finding(
        workspace_id=workspace.id,
        source_id=source.id,
        fingerprint="fp-1",
        source_finding_id="existing",
        title="Outdated TLS configuration on synthetic-web-01",
        severity=SeverityLevel.HIGH,
        asset_id=asset.id,
        exposure_id=exposure.id,
    )
    db_session.add(existing)
    db_session.commit()

    new_finding = Finding(
        workspace_id=workspace.id,
        source_id=source.id,
        fingerprint="fp-2",
        source_finding_id="new",
        title="Outdated TLS configuration on synthetic-web-02",
        severity=SeverityLevel.HIGH,
        asset_id=asset.id,
    )
    db_session.add(new_finding)
    db_session.flush()

    correlate_new_finding(db_session, new_finding)

    assert new_finding.exposure_id == exposure.id
