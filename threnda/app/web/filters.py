"""Jinja2 template filters shared across the web UI."""

from __future__ import annotations

from datetime import datetime

from jinja2 import Environment

from threnda.app.models.enums import SeverityLevel

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


def register_filters(env: Environment) -> None:
    env.filters["severity_class"] = severity_class
    env.filters["severity_letter"] = severity_letter
    env.filters["status_class"] = status_class
    env.filters["humanize_age"] = humanize_age
    env.filters["humanize_dt"] = humanize_dt
    env.filters["risk_class"] = risk_class
