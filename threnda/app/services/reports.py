"""The client-shareable status report for one project.

Built from the same data as the queue, but only from client-visible
content: no internal comments, no internal attachments, no import/source
internals. Used by both the consultancy UI (``/report``) and the client
portal, so the consultant and the client always see the same numbers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from threnda.app.models.enums import ExposureStatus, SeverityLevel
from threnda.app.models.exposure import Exposure
from threnda.app.models.mixins import utcnow
from threnda.app.models.retest import Retest
from threnda.app.models.workspace import Workspace
from threnda.app.services.exposures import ACTIVE_STATUSES

SEVERITY_ORDER = [
    SeverityLevel.CRITICAL,
    SeverityLevel.HIGH,
    SeverityLevel.MEDIUM,
    SeverityLevel.LOW,
    SeverityLevel.INFORMATIONAL,
]
STATUS_ORDER = [
    ExposureStatus.OPEN,
    ExposureStatus.ACKNOWLEDGED,
    ExposureStatus.IN_PROGRESS,
    ExposureStatus.FIX_REPORTED,
    ExposureStatus.RISK_ACCEPTED,
    ExposureStatus.RESOLVED,
]


@dataclass(frozen=True)
class ReportRow:
    exposure: Exposure
    overdue: bool
    last_retest: Retest | None


@dataclass
class ProjectReport:
    workspace: Workspace
    generated_at: datetime
    rows: list[ReportRow] = field(default_factory=list)
    matrix: dict[SeverityLevel, dict[ExposureStatus, int]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.rows)

    def _count(self, predicate: Callable[[ReportRow], bool]) -> int:
        return sum(1 for r in self.rows if predicate(r))

    @property
    def active(self) -> int:
        return self._count(lambda r: r.exposure.status in ACTIVE_STATUSES)

    @property
    def critical_active(self) -> int:
        return self._count(
            lambda r: (
                r.exposure.status in ACTIVE_STATUSES
                and r.exposure.severity == SeverityLevel.CRITICAL
            )
        )

    @property
    def overdue(self) -> int:
        return self._count(lambda r: r.overdue)

    @property
    def awaiting_retest(self) -> int:
        return self._count(lambda r: r.exposure.status == ExposureStatus.FIX_REPORTED)

    @property
    def verified_closed(self) -> int:
        return self._count(lambda r: r.exposure.status == ExposureStatus.RESOLVED)

    @property
    def risk_accepted(self) -> int:
        return self._count(lambda r: r.exposure.status == ExposureStatus.RISK_ACCEPTED)


def is_overdue(exposure: Exposure, today: date) -> bool:
    return (
        exposure.status in ACTIVE_STATUSES
        and exposure.remediation_target_date is not None
        and exposure.remediation_target_date < today
    )


def build_project_report(db: Session, workspace: Workspace) -> ProjectReport:
    today = utcnow().date()
    exposures = (
        db.execute(
            select(Exposure)
            .where(Exposure.workspace_id == workspace.id)
            .options(undefer(Exposure.affected_system))
        )
        .scalars()
        .all()
    )
    latest_retest: dict[int, Retest] = {}
    for retest in db.execute(
        select(Retest)
        .where(Retest.workspace_id == workspace.id)
        .order_by(Retest.performed_at, Retest.id)
    ).scalars():
        latest_retest[retest.exposure_id] = retest

    sev_rank = {s: i for i, s in enumerate(SEVERITY_ORDER)}
    status_rank = {s: i for i, s in enumerate(STATUS_ORDER)}
    ordered = sorted(
        exposures, key=lambda e: (status_rank[e.status] >= 4, sev_rank[e.severity], e.id)
    )
    report = ProjectReport(workspace=workspace, generated_at=utcnow())
    report.matrix = {s: dict.fromkeys(STATUS_ORDER, 0) for s in SEVERITY_ORDER}
    for exposure in ordered:
        report.matrix[exposure.severity][exposure.status] += 1
        report.rows.append(
            ReportRow(
                exposure=exposure,
                overdue=is_overdue(exposure, today),
                last_retest=latest_retest.get(exposure.id),
            )
        )
    return report
