"""Generic importer interface.

Every format-specific importer normalizes raw bytes into
``NormalizedFinding`` objects through a single ``parse()`` call, plus a
cheap ``detect()`` used to pick which importer applies to an upload:

- ``detect()`` — quick, cheap sniff: could this importer plausibly handle
  this filename/content-type/content? Never raises.
- ``parse()`` — do the real work. Raises ``ImportFormatError`` if the input
  isn't this format *at all* (e.g. invalid JSON syntax, a CSV with no
  header row) — that's a whole-import failure. A problem with one
  individual record (missing required field, invalid severity) is *not*
  a format error: it's recorded in ``ParsedImport.errors`` and parsing
  continues, so one bad row doesn't discard an otherwise-good import.

This collapses the spec's four-stage "detect / validate / parse /
normalize" into two calls rather than four separate ones — validation and
normalization are naturally part of producing (or rejecting) each
individual record during parsing, and keeping them as separate top-level
steps here would just mean threading the same intermediate state through
more function boundaries for no behavioural difference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from threnda.app.models.enums import AssetType, SeverityLevel


class ImportFormatError(Exception):
    """Raised when the input cannot be parsed as this format at all.

    Distinct from a per-record problem (see ``RowError``): this means the
    whole upload is rejected, because there is nothing usable to import.
    """


class DuplicateIdentityError(Exception):
    """Raised when one parsed import contains two findings with the same
    ``source_finding_id`` for the same source.

    Handled identically to ``ImportFormatError`` by the upload API: recorded
    as a ``FAILED`` ``ImportRun`` and reported as ``422``, never as a raw
    database error. Detected before any ``Finding``/``ImportRun`` row is
    written (see
    ``threnda.app.services.ingestion.runner.find_duplicate_source_finding_ids``)
    — the alternative, letting two colliding inserts hit
    ``uq_findings_workspace_source_finding_id``, would surface as an
    unhandled `IntegrityError` (a 500) and could leave a partially-written
    import behind.
    """


@dataclass(frozen=True)
class NormalizedAsset:
    """The asset a finding was found on, as extracted by an importer.

    ``canonical_key`` must be stable across repeated imports of the same
    underlying asset (e.g. a normalized hostname or repository path) — see
    docs/community-v0.1-spec.md, "DEDUPLICATION".
    """

    canonical_key: str
    name: str
    asset_type: AssetType = AssetType.UNKNOWN


@dataclass(frozen=True)
class NormalizedFinding:
    """A single finding, normalized to Threnda's common shape.

    ``source_finding_id`` must be stable across repeated imports of the same
    source (used by the Milestone 3 differential engine to tell "same
    finding, seen again" from "new finding"). ``fingerprint`` is a
    provisional, deterministic identity used until Milestone 3's layered
    deduplication replaces it — see ``threnda.app.services.ingestion.fingerprint``.
    """

    source_finding_id: str
    fingerprint: str
    title: str
    severity: SeverityLevel
    description: str | None = None
    cvss_score: float | None = None
    cve: str | None = None
    cwe: str | None = None
    evidence: str | None = None
    remediation: str | None = None
    references: tuple[str, ...] = ()
    asset: NormalizedAsset | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RowError:
    """A single record that failed to normalize. Does not fail the whole import."""

    index: int
    message: str


@dataclass(frozen=True)
class ParsedImport:
    findings: tuple[NormalizedFinding, ...]
    errors: tuple[RowError, ...]
    total_records: int


class Importer(Protocol):
    """Interface every format-specific importer implements."""

    format_name: str

    def detect(self, filename: str, content_type: str | None, raw: bytes) -> bool:
        """Cheap sniff: could this importer plausibly handle this input? Never raises."""
        ...

    def parse(self, raw: bytes) -> ParsedImport:
        """Parse ``raw`` into normalized findings.

        Raises ``ImportFormatError`` if the input isn't this format at all.
        """
        ...
