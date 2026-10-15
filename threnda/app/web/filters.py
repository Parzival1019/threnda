"""Jinja2 template filters shared across the web UI."""

from __future__ import annotations

from datetime import datetime

from jinja2 import Environment

from threnda.app.models.enums import (
    ExposureHistoryEventType,
    ExposureStatus,
    SeverityLevel,
    WorkspaceRole,
)

_SEVERITY_LETTER = {
    SeverityLevel.CRITICAL: "C",
    SeverityLevel.HIGH: "H",
    SeverityLevel.MEDIUM: "M",
    SeverityLevel.LOW: "L",
    SeverityLevel.INFORMATIONAL: "I",
}


def severity_class(severity: SeverityLevel) -> str:
    return f"sev-{severity.value}"


def severity_letter(severity: SeverityLevel) -> str:
    return _SEVERITY_LETTER.get(severity, "?")


def status_class(status: object) -> str:
    value = status.value if hasattr(status, "value") else str(status)
    return f"status-{value}"


def humanize_age(value: datetime | None, *, now: datetime | None = None) -> str:
    """Render a timestamp as a short relative age, e.g. ``3d``, ``5h``, ``12m``."""
    if value is None:
        return "—"
    reference = now or datetime.now(value.tzinfo)
    delta = reference - value
    seconds = int(delta.total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 3600:
        return f"{max(seconds // 60, 0)}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def humanize_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%d %b %Y %H:%M")


def risk_class(score: int) -> str:
    """CSS modifier for a risk score badge — purely a display tier, not a
    scoring input (see threnda.app.services.risk_scoring for the real
    thresholds)."""
    if score >= 70:
        return "risk-high"
    if score >= 40:
        return "risk-medium"
    return ""


#: Human labels for the lifecycle. "Fixed — awaiting retest" and "Verified
#: closed" are deliberately distinct wordings: see docs/finding-lifecycle.md.
STATUS_LABELS = {
    ExposureStatus.OPEN: "Open",
    ExposureStatus.ACKNOWLEDGED: "Acknowledged",
    ExposureStatus.IN_PROGRESS: "In progress",
    ExposureStatus.FIX_REPORTED: "Fixed — awaiting retest",
    ExposureStatus.RISK_ACCEPTED: "Risk accepted",
    ExposureStatus.RESOLVED: "Verified closed",
}

ROLE_LABELS = {
    WorkspaceRole.ADMIN: "Consultancy admin",
    WorkspaceRole.ANALYST: "Pentester",
    WorkspaceRole.VIEWER: "Viewer (internal, read-only)",
    WorkspaceRole.CLIENT: "Client user",
}

_EVENT_LABELS = {
    ExposureHistoryEventType.STATUS_CHANGED: "Status changed",
    ExposureHistoryEventType.ACKNOWLEDGED: "Acknowledged flag",
    ExposureHistoryEventType.OWNER_CHANGED: "Owner changed",
    ExposureHistoryEventType.REMEDIATION_DATE_CHANGED: "Due date changed",
    ExposureHistoryEventType.REMEDIATION_CHANGED: "Remediation changed",
    ExposureHistoryEventType.FINDING_CORRELATED: "Finding correlated",
    ExposureHistoryEventType.REOPENED: "Reopened",
    ExposureHistoryEventType.RESOLVED: "Resolved",
    ExposureHistoryEventType.NOTE_ADDED: "Internal note",
    ExposureHistoryEventType.CREATED: "Created",
    ExposureHistoryEventType.DETAILS_CHANGED: "Details changed",
    ExposureHistoryEventType.FIX_REPORTED: "Marked fixed — awaiting retest",
    ExposureHistoryEventType.RETEST_PASSED: "Retest passed — verified closed",
    ExposureHistoryEventType.RETEST_FAILED: "Retest failed — reopened",
    ExposureHistoryEventType.COMMENT_ADDED: "Comment added",
    ExposureHistoryEventType.ATTACHMENT_ADDED: "Evidence attached",
}


def status_label(status: object) -> str:
    if isinstance(status, ExposureStatus):
        return STATUS_LABELS[status]
    try:
        return STATUS_LABELS[ExposureStatus(str(status))]
    except ValueError:
        return str(status)


def role_label(role: WorkspaceRole) -> str:
    return ROLE_LABELS.get(role, role.value)


def event_label(event: object) -> str:
    """Timeline label. Import-differential events (``actor == "system"``)
    describe the *source data*, not a workflow decision, and say so."""
    event_type = getattr(event, "event_type", None)
    actor = getattr(event, "actor", None)
    if actor == "system" and event_type == ExposureHistoryEventType.RESOLVED:
        return "Not in latest import (source data only — status unchanged)"
    if actor == "system" and event_type == ExposureHistoryEventType.REOPENED:
        return "Reappeared in an import (source data)"
    if isinstance(event_type, ExposureHistoryEventType):
        return _EVENT_LABELS.get(event_type, event_type.value.replace("_", " "))
    return str(event_type)


def filesize(num: int) -> str:
    if num < 1024:
        return f"{num} B"
    if num < 1024 * 1024:
        return f"{num / 1024:.0f} KB"
    return f"{num / (1024 * 1024):.1f} MB"


def register_filters(env: Environment) -> None:
    env.filters["severity_class"] = severity_class
    env.filters["severity_letter"] = severity_letter
    env.filters["status_class"] = status_class
    env.filters["humanize_age"] = humanize_age
    env.filters["humanize_dt"] = humanize_dt
    env.filters["risk_class"] = risk_class
    env.filters["status_label"] = status_label
    env.filters["role_label"] = role_label
    env.filters["event_label"] = event_label
    env.filters["filesize"] = filesize
