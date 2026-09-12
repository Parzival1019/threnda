"""Unit tests for the extracted ``apply_exposure_update`` service.

This is the same audited update logic ``PATCH /api/v1/exposures/{id}``
already exercises end-to-end (``tests/test_api_exposures.py``); these tests
cover it directly since Milestone 5's web UI calls it without going through
that HTTP endpoint at all.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from threnda.app.models.enums import ExposureHistoryEventType, ExposureStatus, SeverityLevel
from threnda.app.models.exposure import Exposure
from threnda.app.models.workspace import Workspace
from threnda.app.services.exposures import apply_exposure_update


def _make_exposure(db: Session) -> Exposure:
    workspace = Workspace(name="synthetic-exposure-service")
    db.add(workspace)
    db.flush()
    exposure = Exposure(
        workspace_id=workspace.id,
        canonical_fingerprint="svc-fp-1",
        title="Synthetic exposure for service tests",
        severity=SeverityLevel.HIGH,
    )
    db.add(exposure)
    db.flush()
    return exposure


def test_status_change_to_resolved_sets_resolved_at_and_audits(db_session: Session) -> None:
    exposure = _make_exposure(db_session)

    history = apply_exposure_update(
        db_session,
        exposure,
        {"status": ExposureStatus.RESOLVED},
        note=None,
        actor="tester",
    )

    assert exposure.status == ExposureStatus.RESOLVED
    assert exposure.resolved_at is not None
    assert len(history) == 1
    assert history[0].event_type == ExposureHistoryEventType.RESOLVED
    assert history[0].actor == "tester"


def test_reopening_a_resolved_exposure_clears_resolved_at(db_session: Session) -> None:
    exposure = _make_exposure(db_session)
    apply_exposure_update(
        db_session, exposure, {"status": ExposureStatus.RESOLVED}, note=None, actor="tester"
    )
    assert exposure.resolved_at is not None

    history = apply_exposure_update(
        db_session, exposure, {"status": ExposureStatus.OPEN}, note=None, actor="tester"
    )

    assert exposure.status == ExposureStatus.OPEN
    assert exposure.resolved_at is None
    assert history[0].event_type == ExposureHistoryEventType.REOPENED


def test_no_op_write_produces_no_audit_row(db_session: Session) -> None:
    exposure = _make_exposure(db_session)

    history = apply_exposure_update(
        db_session, exposure, {"status": exposure.status}, note=None, actor="tester"
    )

    assert history == []


def test_owner_and_acknowledged_are_independent_and_both_audited(db_session: Session) -> None:
    exposure = _make_exposure(db_session)

    history = apply_exposure_update(
        db_session,
        exposure,
        {"owner": "alice", "acknowledged": True},
        note=None,
        actor="tester",
    )

    assert exposure.owner == "alice"
    assert exposure.acknowledged is True
    assert exposure.status == ExposureStatus.OPEN
    event_types = {entry.event_type for entry in history}
    assert event_types == {
        ExposureHistoryEventType.OWNER_CHANGED,
        ExposureHistoryEventType.ACKNOWLEDGED,
    }


def test_remediation_target_date_change_is_audited_with_isoformat_values(
    db_session: Session,
) -> None:
    exposure = _make_exposure(db_session)
    target = date(2026, 12, 1)

    history = apply_exposure_update(
        db_session,
        exposure,
        {"remediation_target_date": target},
        note=None,
        actor="tester",
    )

    assert exposure.remediation_target_date == target
    assert history[0].previous_value is None
    assert history[0].new_value == target.isoformat()


def test_note_always_appends_regardless_of_other_changes(db_session: Session) -> None:
    exposure = _make_exposure(db_session)

    history = apply_exposure_update(
        db_session, exposure, {}, note="Investigating with the platform team.", actor="tester"
    )

    assert len(history) == 1
    assert history[0].event_type == ExposureHistoryEventType.NOTE_ADDED
    assert history[0].new_value == "Investigating with the platform team."
