"""Severity and CVSS normalization shared by every importer.

Centralized so "high" vs "High" vs "HIGH" vs "severe" are all handled
identically regardless of which importer sees them, and so CVSS validation
rules (finite, 0.0-10.0) are enforced in exactly one place.
"""

from __future__ import annotations

import math

from threnda.app.models.enums import SeverityLevel

_SEVERITY_ALIASES: dict[str, SeverityLevel] = {
    "informational": SeverityLevel.INFORMATIONAL,
    "information": SeverityLevel.INFORMATIONAL,
    "info": SeverityLevel.INFORMATIONAL,
    "none": SeverityLevel.INFORMATIONAL,
    "low": SeverityLevel.LOW,
    "minor": SeverityLevel.LOW,
    "medium": SeverityLevel.MEDIUM,
    "moderate": SeverityLevel.MEDIUM,
    "warning": SeverityLevel.MEDIUM,
    "high": SeverityLevel.HIGH,
    "critical": SeverityLevel.CRITICAL,
    "severe": SeverityLevel.CRITICAL,
    "error": SeverityLevel.HIGH,
}


def normalize_severity_name(raw: str | None) -> SeverityLevel | None:
    """Map a free-text severity name to a `SeverityLevel`, or `None` if unrecognized."""
    if raw is None:
        return None
    key = raw.strip().lower()
    if not key:
        return None
    try:
        return SeverityLevel(key)
    except ValueError:
        return _SEVERITY_ALIASES.get(key)


def severity_from_cvss(score: float) -> SeverityLevel:
    """Derive a severity band from a CVSS score using the standard v3 ranges.

    Used when a source gives a numeric score but no severity label of its
    own (or as a documented fallback when the label is unrecognized).
    """
    if score <= 0.0:
        return SeverityLevel.INFORMATIONAL
    if score < 4.0:
        return SeverityLevel.LOW
    if score < 7.0:
        return SeverityLevel.MEDIUM
    if score < 9.0:
        return SeverityLevel.HIGH
    return SeverityLevel.CRITICAL


def parse_cvss_score(raw: str | float | int | None) -> float:
    """Parse and validate a CVSS score.

    Raises ``ValueError`` for anything that isn't a finite number in
    [0.0, 10.0] — never returns NaN/inf and never silently clamps an
    out-of-range value, so a malformed score becomes a reported row error
    instead of a corrupted-looking finding.
    """
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        raise ValueError("CVSS score is required")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"CVSS score {raw!r} is not a number") from exc
    if not math.isfinite(value):
        raise ValueError(f"CVSS score {raw!r} is not a finite number")
    if not (0.0 <= value <= 10.0):
        raise ValueError(f"CVSS score {value} is out of the valid 0.0-10.0 range")
    return value
