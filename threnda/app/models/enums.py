"""Vendor-neutral enumerations shared by the domain model.

Every enum is stored as a plain lowercase ``VARCHAR`` with a ``CHECK``
constraint (``native_enum=False``) rather than a native database enum type.
SQLite has no native enum support, and evolving a PostgreSQL native enum
(adding a value) needs special-cased migrations; a ``VARCHAR`` + ``CHECK``
behaves identically on both databases and is simpler to change later.
"""

from __future__ import annotations

import enum
from collections.abc import Callable

from sqlalchemy import Enum as SAEnum


def sa_enum(enum_cls: type[enum.Enum], *, length: int = 50) -> SAEnum:
    """Build a portable, value-based SQLAlchemy enum column type.

    ``create_constraint=True`` is required for ``native_enum=False`` to
    actually emit a ``CHECK`` constraint. Without it, SQLAlchemy silently
    stores the column as a plain unconstrained ``VARCHAR`` — the type is
    documentation only, and raw SQL (or a bug in application code) can write
    any string. This was reported in the Milestone 1 review as findings
    accepting an arbitrary severity string.
    """
    values_callable: Callable[[type[enum.Enum]], list[str]] = lambda obj: [e.value for e in obj]  # noqa: E731
    return SAEnum(
        enum_cls,
        values_callable=values_callable,
        native_enum=False,
        create_constraint=True,
        length=length,
    )


class SeverityLevel(enum.StrEnum):
    """Common severity scale used across all finding sources."""

    INFORMATIONAL = "informational"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SourceType(enum.StrEnum):
    """Where a set of findings originated from.

    This list is deliberately not exhaustive of every future integration —
    see docs/community-v0.1-spec.md for the ingestion roadmap. Live vendor
    integrations are Enterprise-only; Community only ever reads files.
    """

    CSV = "csv"
    JSON = "json"
    SARIF = "sarif"
    PENTEST_REPORT = "pentest_report"
    TRIVY = "trivy"
    NUCLEI = "nuclei"
    SEMGREP = "semgrep"
    OTHER = "other"


class ImportStatus(enum.StrEnum):
    """Lifecycle of a single ``ImportRun``."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AssetType(enum.StrEnum):
    HOST = "host"
    IP = "ip"
    URL = "url"
    APPLICATION = "application"
    REPOSITORY = "repository"
    CONTAINER_IMAGE = "container_image"
    CONTAINER = "container"
    KUBERNETES_WORKLOAD = "kubernetes_workload"
    CLOUD_RESOURCE = "cloud_resource"
    UNKNOWN = "unknown"


class AssetEnvironment(enum.StrEnum):
    """Deployment environment of an asset, used later as a risk-scoring input."""

    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"
    TEST = "test"
    UNKNOWN = "unknown"


class AssetCriticality(enum.StrEnum):
    """Manually-assigned business importance of an asset.

    Unlike ``AssetEnvironment`` (sometimes inferrable from naming/tooling),
    this is always a human judgement call — see docs/community-v0.1-spec.md,
    "RISK SCORING" ("manual business criticality") — so it has no importer
    path and defaults to ``UNKNOWN`` until someone sets it.
    """

    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExposureStatus(enum.StrEnum):
    """Remediation workflow status of an ``Exposure``.

    See "FINDING LIFECYCLE" in docs/community-v0.1-spec.md — the statuses
    are defined there against findings conceptually, but users work
    exposures, so the status lives on ``Exposure``.
    """

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    IN_PROGRESS = "in_progress"
    RISK_ACCEPTED = "risk_accepted"
    RESOLVED = "resolved"


class ExposureHistoryEventType(enum.StrEnum):
    """What kind of change an ``ExposureHistory`` row records."""

    STATUS_CHANGED = "status_changed"
    ACKNOWLEDGED = "acknowledged"
    OWNER_CHANGED = "owner_changed"
    REMEDIATION_DATE_CHANGED = "remediation_date_changed"
    REMEDIATION_CHANGED = "remediation_changed"
    FINDING_CORRELATED = "finding_correlated"
    REOPENED = "reopened"
    RESOLVED = "resolved"
    NOTE_ADDED = "note_added"


class PossibleDuplicateStatus(enum.StrEnum):
    """Review state of a ``PossibleDuplicate`` candidate.

    ``POSSIBLE``-confidence matches are never auto-merged (see
    docs/community-v0.1-spec.md, "DEDUPLICATION") — a human decides.
    """

    PENDING = "pending"
    MERGED = "merged"
    KEPT_SEPARATE = "kept_separate"


class DocumentContentType(enum.StrEnum):
    """What kind of file a ``Document`` was extracted from."""

    PDF = "pdf"
    TEXT = "text"


class DocumentStatus(enum.StrEnum):
    """Outcome of a ``Document``'s local text extraction.

    See docs/pentest-and-ai.md — extraction is deterministic and entirely
    local; ``FAILED`` covers a malformed/encrypted/unreadable file, not
    anything about the candidates later derived from the text.
    """

    PROCESSED = "processed"
    FAILED = "failed"


class CandidateFindingStatus(enum.StrEnum):
    """Review state of a ``CandidateFinding``.

    Mirrors ``PossibleDuplicateStatus``: a candidate — heuristic or
    AI-assisted — is never automatically promoted to a real ``Finding``.
    See docs/pentest-and-ai.md, "Mandatory human review".
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
