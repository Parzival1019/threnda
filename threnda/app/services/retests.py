"""Retests — the only way a finding becomes *verified closed*.

A pentester (``analyst``) or consultancy admin records the outcome of
re-testing a finding:

- ``passed``  -> status ``RESOLVED`` (verified closed), ``resolved_at`` set;
- ``failed``  -> status ``OPEN`` again (re-opened), ``resolved_at`` cleared.

Both outcomes write an append-only ``Retest`` row and an ``ExposureHistory``
event (``RETEST_PASSED``/``RETEST_FAILED``) carrying the previous and new
status. A client's "we fixed it" (``FIX_REPORTED``) and an import that no
longer contains the finding are *inputs* to a retest, never a substitute
for one — see docs/finding-lifecycle.md.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from threnda.app.models.enums import (
    ExposureHistoryEventType,
    ExposureStatus,
    RetestOutcome,
    WorkspaceRole,
)
from threnda.app.models.exposure import Exposure
from threnda.app.models.exposure_history import ExposureHistory
from threnda.app.models.mixins import utcnow
from threnda.app.models.retest import Retest
from threnda.app.models.user import User
from threnda.app.services.memberships import role_at_least


class RetestNotAllowedError(ValueError):
    """Raised when a retest can't be recorded for this exposure/user."""


def record_retest(
    db: Session,
    exposure: Exposure,
    *,
    outcome: RetestOutcome,
    notes: str | None,
    actor: User,
    actor_role: WorkspaceRole,
) -> Retest:
    # Defence in depth: the routes already require ``analyst``, but the
    # service is the one place the rule "only a pentester/admin can close a
    # finding" must hold no matter who calls it.
    if not role_at_least(actor_role, WorkspaceRole.ANALYST):
        raise RetestNotAllowedError("Only a pentester or consultancy admin can record a retest.")
    previous = exposure.status
    if outcome == RetestOutcome.PASSED and previous == ExposureStatus.RESOLVED:
        raise RetestNotAllowedError("This finding is already verified closed.")
    if outcome == RetestOutcome.PASSED and previous == ExposureStatus.RISK_ACCEPTED:
        raise RetestNotAllowedError(
            "This finding is risk-accepted; reopen it before recording a passed retest."
        )

    cleaned_notes = (notes or "").strip() or None
    retest = Retest(
        workspace_id=exposure.workspace_id,
        exposure_id=exposure.id,
        outcome=outcome,
        previous_status=previous,
        notes=cleaned_notes,
        performed_by_user_id=actor.id,
        performed_by_username=actor.username,
    )
    db.add(retest)

    if outcome == RetestOutcome.PASSED:
        exposure.status = ExposureStatus.RESOLVED
        exposure.resolved_at = utcnow()
        event = ExposureHistoryEventType.RETEST_PASSED
    else:
        exposure.status = ExposureStatus.OPEN
        exposure.resolved_at = None
        event = ExposureHistoryEventType.RETEST_FAILED

    detail = f" — {cleaned_notes}" if cleaned_notes else ""
    db.add(
        ExposureHistory(
            exposure=exposure,
            event_type=event,
            previous_value=previous.value,
            new_value=f"{exposure.status.value}{detail}",
            actor=actor.username,
        )
    )
    db.flush()
    return retest
