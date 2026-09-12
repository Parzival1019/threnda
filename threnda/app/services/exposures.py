"""Shared exposure-update logic — the audited partial-update workflow.

Extracted from ``api/v1/exposures.py`` so the JSON API and the server-
rendered web UI (Milestone 5) apply exactly the same audited update
behaviour instead of two independently-maintained copies of it.
"""

from __future__ import annotations

from datetime import date
from typing import TypedDict

from sqlalchemy.orm import Session

from threnda.app.models.enums import ExposureHistoryEventType, ExposureStatus
from threnda.app.models.exposure import Exposure
from threnda.app.models.exposure_history import ExposureHistory
from threnda.app.models.mixins import utcnow


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
        event_type = ExposureHistoryEventType.STATUS_CHANGED
        if new_status == ExposureStatus.RESOLVED:
            exposure.resolved_at = utcnow()
            event_type = ExposureHistoryEventType.RESOLVED
        elif previous_status == ExposureStatus.RESOLVED:
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
    db.refresh(exposure)
    return history
