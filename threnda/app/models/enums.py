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
    MANUAL = "manual"
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

    ``FIX_REPORTED`` ("fixed, awaiting retest") and ``RESOLVED`` ("verified
    closed") are deliberately separate: a client saying "we fixed it", or a
    finding disappearing from a later import, never by itself closes
    anything. ``RESOLVED`` is only reachable through a recorded retest by a
    pentester/admin — see docs/finding-lifecycle.md and
    ``threnda.app.services.retests``.
    """

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    IN_PROGRESS = "in_progress"
    FIX_REPORTED = "fix_reported"
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
    CREATED = "created"
    DETAILS_CHANGED = "details_changed"
    FIX_REPORTED = "fix_reported"
    RETEST_PASSED = "retest_passed"
    RETEST_FAILED = "retest_failed"
    COMMENT_ADDED = "comment_added"
    ATTACHMENT_ADDED = "attachment_added"


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


class WorkspaceRole(enum.StrEnum):
    """A user's permission level within one workspace.

    See docs/security.md, "Roles" — ``ADMIN`` manages membership and every
    ``ANALYST`` capability; ``ANALYST`` operates findings/imports/documents/
    exports; ``VIEWER`` is read-only. Roles are per-membership, not global —
    the same user can be an admin of one workspace and a viewer of another.
    """

    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"
    # The client-side user of a consultancy engagement. Deliberately the
    # *lowest* role in the hierarchy (see
    # ``threnda.app.services.memberships``): every pre-existing route that
    # requires ``viewer`` therefore refuses it automatically, and a client
    # user only reaches the client portal and the handful of API routes that
    # explicitly opt in with ``require_client`` — see docs/security.md,
    # "Client users".
    CLIENT = "client"


class AuditEventType(enum.StrEnum):
    """What kind of security-sensitive event an ``AuditLogEntry`` records.

    Deliberately a closed, controlled vocabulary (like every other event-type
    enum in this schema) rather than a free-form string — see
    docs/security.md, "Auditing".
    """

    USER_CREATED = "user_created"
    USER_DEACTIVATED = "user_deactivated"
    USER_REACTIVATED = "user_reactivated"
    LOGIN_SUCCEEDED = "login_succeeded"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    MEMBER_ADDED = "member_added"
    MEMBER_ROLE_CHANGED = "member_role_changed"
    MEMBER_REMOVED = "member_removed"
    WEBHOOK_CREATED = "webhook_created"
    WEBHOOK_UPDATED = "webhook_updated"
    WEBHOOK_DELETED = "webhook_deleted"
    WEBHOOK_DELIVERY_TRIGGERED = "webhook_delivery_triggered"
    EXPORT_DOWNLOADED = "export_downloaded"
    CLIENT_CREATED = "client_created"
    PROJECT_CREATED = "project_created"
    INVITATION_CREATED = "invitation_created"
    INVITATION_ACCEPTED = "invitation_accepted"
    INVITATION_REVOKED = "invitation_revoked"
    ATTACHMENT_DOWNLOADED = "attachment_downloaded"
    REPORT_VIEWED = "report_viewed"
    DEMO_RESET = "demo_reset"


class CandidateFindingStatus(enum.StrEnum):
    """Review state of a ``CandidateFinding``.

    Mirrors ``PossibleDuplicateStatus``: a candidate — heuristic or
    AI-assisted — is never automatically promoted to a real ``Finding``.
    See docs/pentest-and-ai.md, "Mandatory human review".
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class RetestOutcome(enum.StrEnum):
    """Result a pentester records for one retest of an ``Exposure``."""

    PASSED = "passed"
    FAILED = "failed"
