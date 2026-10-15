"""Shared exposure-update logic — the audited partial-update workflow.

Extracted from ``api/v1/exposures.py`` so the JSON API and the server-
rendered web UI (Milestone 5) apply exactly the same audited update
behaviour instead of two independently-maintained copies of it.
"""

from __future__ import annotations

from datetime import date
from typing import TypedDict

from sqlalchemy.orm import Session

from threnda.app.models.enums import ExposureHistoryEventType, ExposureStatus, SeverityLevel
from threnda.app.models.exposure import Exposure
from threnda.app.models.exposure_history import ExposureHistory
from threnda.app.services.risk_scoring import recompute_and_persist_risk_score


class ExposureChanges(TypedDict, total=False):
    """Partial update payload — only keys the caller actually intends to set.

    Mirrors ``ExposureUpdate`` (minus ``note``, handled separately since it
    never reads an existing field). Build with e.g.
    ``payload.model_dump(exclude_unset=True, exclude={"note"})``.
    """

    status: ExposureStatus
    owner: str | None
    acknowledged: bool
    remediation_target_date: date | None
    remediation: str | None
    title: str
    description: str | None
    severity: SeverityLevel
    affected_system: str | None


class InvalidStatusTransitionError(Exception):  # noqa: N818 - established public name
    """Raised when an update would move ``status`` along a forbidden edge.

    See docs/risk-scoring.md's sibling doc, docs/implementation-plan.md
    Milestone 4, for the full graph and the reasoning behind it.
    """

    def __init__(self, current: ExposureStatus, requested: ExposureStatus) -> None:
        self.current = current
        self.requested = requested
        allowed = sorted(s.value for s in allowed_next_statuses(current))
        super().__init__(
            f"Cannot move an exposure from {current.value!r} to {requested.value!r}; "
            f"allowed next status(es) from {current.value!r}: {allowed or 'none'}."
        )


class VerifiedClosureRequiresRetestError(InvalidStatusTransitionError):
    """Raised when a plain status update asks for ``RESOLVED``.

    ``RESOLVED`` means *verified closed*: it is only ever reached through a
    recorded retest (``threnda.app.services.retests.record_retest``), never
    through a generic status change — not by a client saying "fixed", not
    by a finding disappearing from an import, and not by a pentester simply
    picking "resolved" from a list. See docs/finding-lifecycle.md.
    """

    def __init__(self, current: ExposureStatus) -> None:
        self.current = current
        self.requested = ExposureStatus.RESOLVED
        Exception.__init__(
            self,
            "Verified closure requires a recorded retest — record a passed retest "
            "instead of setting the status to 'resolved' directly.",
        )


# Lifecycle (docs/finding-lifecycle.md):
#
#   OPEN -> ACKNOWLEDGED -> IN_PROGRESS -> FIX_REPORTED --retest passed--> RESOLVED
#                                                      \--retest failed--> OPEN
#
# Every active state may skip ahead to FIX_REPORTED or RISK_ACCEPTED
# (skipping triage steps is a real, common workflow). FIX_REPORTED may go
# back to IN_PROGRESS ("the fix wasn't complete after all") without a
# retest. What the graph forbids: moving backward to OPEN between active
# states without a retest/reopen, and *any* generic edge into RESOLVED —
# that edge exists only inside the retest service. The only edge out of
# RESOLVED or RISK_ACCEPTED is an explicit reopen back to OPEN.
ALLOWED_STATUS_TRANSITIONS: dict[ExposureStatus, frozenset[ExposureStatus]] = {
    ExposureStatus.OPEN: frozenset(
        {
            ExposureStatus.ACKNOWLEDGED,
            ExposureStatus.IN_PROGRESS,
            ExposureStatus.FIX_REPORTED,
            ExposureStatus.RISK_ACCEPTED,
        }
    ),
    ExposureStatus.ACKNOWLEDGED: frozenset(
        {ExposureStatus.IN_PROGRESS, ExposureStatus.FIX_REPORTED, ExposureStatus.RISK_ACCEPTED}
    ),
    ExposureStatus.IN_PROGRESS: frozenset(
        {ExposureStatus.FIX_REPORTED, ExposureStatus.RISK_ACCEPTED}
    ),
    ExposureStatus.FIX_REPORTED: frozenset(
        {ExposureStatus.IN_PROGRESS, ExposureStatus.RISK_ACCEPTED}
    ),
    ExposureStatus.RISK_ACCEPTED: frozenset({ExposureStatus.OPEN}),
    ExposureStatus.RESOLVED: frozenset({ExposureStatus.OPEN}),
}

#: Statuses that still need work — everything except the two closed ones.
ACTIVE_STATUSES = frozenset(
    {
        ExposureStatus.OPEN,
        ExposureStatus.ACKNOWLEDGED,
        ExposureStatus.IN_PROGRESS,
        ExposureStatus.FIX_REPORTED,
    }
)

#: Statuses a closed exposure reopens from — both audit as REOPENED, since
#: the graph above guarantees OPEN is the only edge out of either.
_CLOSED_STATUSES = frozenset({ExposureStatus.RESOLVED, ExposureStatus.RISK_ACCEPTED})


def allowed_next_statuses(current: ExposureStatus) -> frozenset[ExposureStatus]:
    """Statuses ``current`` may move to — used to both validate and to
    build the web UI's status ``<select>`` so it never offers what the API
    would refuse."""
    return ALLOWED_STATUS_TRANSITIONS[current]


def validate_status_transition(current: ExposureStatus, requested: ExposureStatus) -> None:
    if requested == ExposureStatus.RESOLVED:
        raise VerifiedClosureRequiresRetestError(current)
    if requested not in allowed_next_statuses(current):
        raise InvalidStatusTransitionError(current, requested)


def apply_exposure_update(
    db: Session,
    exposure: Exposure,
    changes: ExposureChanges,
    *,
    note: str | None,
    actor: str,
) -> list[ExposureHistory]:
    """Apply a partial update to ``exposure`` and audit every real change.

    A key present in ``changes`` but equal to the current value produces no
    audit row — "no-op writes are not audited". ``note`` always appends a
    ``NOTE_ADDED`` entry when given.
    """
    history: list[ExposureHistory] = []

    if "status" in changes and changes["status"] != exposure.status:
        previous_status = exposure.status
        new_status = changes["status"]
        validate_status_transition(previous_status, new_status)
        event_type = ExposureHistoryEventType.STATUS_CHANGED
        if new_status == ExposureStatus.FIX_REPORTED:
            event_type = ExposureHistoryEventType.FIX_REPORTED
        elif previous_status in _CLOSED_STATUSES:
            exposure.resolved_at = None
            event_type = ExposureHistoryEventType.REOPENED
        exposure.status = new_status
        history.append(
            ExposureHistory(
                exposure=exposure,
                event_type=event_type,
                previous_value=previous_status.value,
                new_value=new_status.value,
                actor=actor,
            )
        )

    if "owner" in changes and changes["owner"] != exposure.owner:
        new_owner = changes["owner"]
        history.append(
            ExposureHistory(
                exposure=exposure,
                event_type=ExposureHistoryEventType.OWNER_CHANGED,
                previous_value=exposure.owner,
                new_value=new_owner,
                actor=actor,
            )
        )
        exposure.owner = new_owner

    # `acknowledged` and `status` are independent fields per the spec's
    # Exposure model (status is the lifecycle stage; acknowledged is a
    # simple flag) — this does not cross-derive one from the other, and
    # both directions of the flag are audited the same way.
    if "acknowledged" in changes and changes["acknowledged"] != exposure.acknowledged:
        previous_acknowledged = exposure.acknowledged
        new_acknowledged = changes["acknowledged"]
        history.append(
            ExposureHistory(
                exposure=exposure,
                event_type=ExposureHistoryEventType.ACKNOWLEDGED,
                previous_value=str(previous_acknowledged).lower(),
                new_value=str(new_acknowledged).lower(),
                actor=actor,
            )
        )
        exposure.acknowledged = new_acknowledged

    if "remediation_target_date" in changes and (
        changes["remediation_target_date"] != exposure.remediation_target_date
    ):
        previous_date = exposure.remediation_target_date
        new_date = changes["remediation_target_date"]
        history.append(
            ExposureHistory(
                exposure=exposure,
                event_type=ExposureHistoryEventType.REMEDIATION_DATE_CHANGED,
                previous_value=previous_date.isoformat() if previous_date else None,
                new_value=new_date.isoformat() if new_date else None,
                actor=actor,
            )
        )
        exposure.remediation_target_date = new_date

    if "remediation" in changes and changes["remediation"] != exposure.remediation:
        new_remediation = changes["remediation"]
        history.append(
            ExposureHistory(
                exposure=exposure,
                event_type=ExposureHistoryEventType.REMEDIATION_CHANGED,
                previous_value=exposure.remediation,
                new_value=new_remediation,
                actor=actor,
            )
        )
        exposure.remediation = new_remediation

    # Finding details (manual findings, or correcting an imported title).
    # One DETAILS_CHANGED row per field, value prefixed with the field name
    # so the timeline reads "severity: high -> critical".
    for field in ("title", "description", "severity", "affected_system"):
        if field not in changes:
            continue
        new_value = changes[field]
        old_value = getattr(exposure, field)
        if new_value == old_value:
            continue
        if field == "title" and not new_value:
            raise ValueError("title cannot be empty")
        history.append(
            ExposureHistory(
                exposure=exposure,
                event_type=ExposureHistoryEventType.DETAILS_CHANGED,
                previous_value=f"{field}: {_display(old_value)}",
                new_value=f"{field}: {_display(new_value)}",
                actor=actor,
            )
        )
        setattr(exposure, field, new_value)

    if note:
        history.append(
            ExposureHistory(
                exposure=exposure,
                event_type=ExposureHistoryEventType.NOTE_ADDED,
                previous_value=None,
                new_value=note,
                actor=actor,
            )
        )

    db.add_all(history)
    db.flush()
    if "severity" in changes and any(
        h.event_type == ExposureHistoryEventType.DETAILS_CHANGED
        and (h.new_value or "").startswith("severity:")
        for h in history
    ):
        # Severity is a risk-score input; keep the persisted score current.
        recompute_and_persist_risk_score(db, exposure)
    db.refresh(exposure)
    return history


def _display(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, SeverityLevel):
        return value.value
    text = str(value)
    return text if len(text) <= 200 else text[:197] + "..."
