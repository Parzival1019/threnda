"""Unit tests for the extracted ``apply_exposure_update`` service.

This is the same audited update logic ``PATCH /api/v1/exposures/{id}``
already exercises end-to-end (``tests/test_api_exposures.py``); these tests
cover it directly since Milestone 5's web UI calls it without going through
that HTTP endpoint at all.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from threnda.app.models.enums import ExposureHistoryEventType, ExposureStatus, SeverityLevel
from threnda.app.models.exposure import Exposure
from threnda.app.models.workspace import Workspace
from threnda.app.services.exposures import (
    ALLOWED_STATUS_TRANSITIONS,
    InvalidStatusTransitionError,
    allowed_next_statuses,
    apply_exposure_update,
    validate_status_transition,
)


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


# --------------------------------------------------------- lifecycle graph


ALL_VALID_EDGES = [
    (current, target)
    for current, targets in ALLOWED_STATUS_TRANSITIONS.items()
    for target in targets
]

# Every (current, target) pair the graph forbids, restricted to pairs that
# are actually different statuses (identity is never "invalid" — it's a
# no-op, validated separately).
ALL_INVALID_EDGES = [
    (current, target)
    for current in ExposureStatus
    for target in ExposureStatus
    if current != target and target not in ALLOWED_STATUS_TRANSITIONS[current]
]


@pytest.mark.parametrize("current,target", ALL_VALID_EDGES)
def test_every_documented_edge_is_allowed(
    db_session: Session, current: ExposureStatus, target: ExposureStatus
) -> None:
    exposure = _make_exposure(db_session)
    exposure.status = current
    db_session.flush()

    history = apply_exposure_update(
        db_session, exposure, {"status": target}, note=None, actor="tester"
    )

    assert exposure.status == target
    assert len(history) == 1


@pytest.mark.parametrize("current,target", ALL_INVALID_EDGES)
def test_every_undocumented_edge_is_rejected(
    db_session: Session, current: ExposureStatus, target: ExposureStatus
) -> None:
    exposure = _make_exposure(db_session)
    exposure.status = current
    db_session.flush()

    with pytest.raises(InvalidStatusTransitionError):
        apply_exposure_update(db_session, exposure, {"status": target}, note=None, actor="tester")

    # Rejected before any mutation — status/history untouched.
    db_session.refresh(exposure)
    assert exposure.status == current
    assert exposure.history == []


def test_allowed_next_statuses_excludes_current_status() -> None:
    for status in ExposureStatus:
        assert status not in allowed_next_statuses(status)


def test_validate_status_transition_is_a_pure_predicate() -> None:
    validate_status_transition(ExposureStatus.OPEN, ExposureStatus.ACKNOWLEDGED)  # does not raise
    with pytest.raises(InvalidStatusTransitionError):
        validate_status_transition(ExposureStatus.ACKNOWLEDGED, ExposureStatus.OPEN)


def test_reopening_from_risk_accepted_also_audits_as_reopened(db_session: Session) -> None:
    exposure = _make_exposure(db_session)
    apply_exposure_update(
        db_session, exposure, {"status": ExposureStatus.RISK_ACCEPTED}, note=None, actor="tester"
    )
    assert exposure.resolved_at is None  # RISK_ACCEPTED never sets resolved_at

    history = apply_exposure_update(
        db_session, exposure, {"status": ExposureStatus.OPEN}, note=None, actor="tester"
    )

    assert exposure.status == ExposureStatus.OPEN
    assert history[0].event_type == ExposureHistoryEventType.REOPENED


def test_setting_status_to_its_current_value_is_a_no_op_not_an_error(
    db_session: Session,
) -> None:
    exposure = _make_exposure(db_session)
    assert exposure.status == ExposureStatus.OPEN

    history = apply_exposure_update(
        db_session, exposure, {"status": ExposureStatus.OPEN}, note=None, actor="tester"
    )

    assert history == []
    assert exposure.status == ExposureStatus.OPEN


def test_repeated_identical_updates_are_idempotent(db_session: Session) -> None:
    exposure = _make_exposure(db_session)
    apply_exposure_update(
        db_session, exposure, {"status": ExposureStatus.ACKNOWLEDGED}, note=None, actor="tester"
    )
    history_count_after_first = len(exposure.history)

    # Re-applying "acknowledged" again (same target) is a no-op the second
    # time, not a repeated audit entry or a raised error.
    apply_exposure_update(
        db_session, exposure, {"status": ExposureStatus.ACKNOWLEDGED}, note=None, actor="tester"
    )

    assert len(exposure.history) == history_count_after_first
    assert exposure.status == ExposureStatus.ACKNOWLEDGED
