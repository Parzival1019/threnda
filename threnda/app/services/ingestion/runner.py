"""Persist a parsed import: the Milestone 3 differential engine.

Classifies every incoming normalized finding against the existing state of
its ``(workspace, source)`` — see docs/community-v0.1-spec.md, "IMPORT
DIFFERENTIALS" — and applies exactly one of:

- **NEW** — no existing finding shares this *source-local identity*
  (``Finding.source_finding_id``, not ``fingerprint`` — see
  docs/deduplication.md for why the two are kept separate) at all: create
  it, and run it through the correlation engine
  (``threnda.app.services.dedup.correlation``) to decide which ``Exposure``
  it belongs to.
- **UNCHANGED** / **UPDATED** — an *active* (not yet resolved) finding
  already has this identity: update its content in place if it differs,
  refresh ``last_seen``. Never re-correlated — it already has whatever
  ``exposure_id`` it was given when first created.
- **REOPENED** — a *resolved* finding has this identity: clear
  ``resolved_at``, update content, refresh ``last_seen``, and audit it
  against its linked exposure (``ExposureHistoryEventType.REOPENED``) if
  it has one. Still never re-correlated, for the same reason.
- **RESOLVED** — an active finding from a *previous* import of this source
  was not present in this one: set ``resolved_at`` and audit it
  (``ExposureHistoryEventType.RESOLVED``) if it has a linked exposure. The
  row is never deleted, and nothing about its ``Exposure`` is touched
  automatically — see docs/deduplication.md for why (this is a deliberate
  Community/Enterprise boundary, not an oversight). Only inferred when this
  import parsed with zero row errors: a row that failed to normalize never
  reaches ``parsed.findings``, so its identity can't be told apart from
  "genuinely gone" here — see the comment above the resolved-inference loop
  below.

Every successfully-parsed record lands in exactly one of NEW/UNCHANGED/
UPDATED/REOPENED; the resulting counts, plus RESOLVED, are stored on the
``ImportRun`` itself. Individually-invalid records (already filtered out by
the importer into ``ParsedImport.errors``) are counted in
``failed_records`` as before and never silently dropped from the summary.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.asset import Asset
from threnda.app.models.enums import ExposureHistoryEventType, ImportStatus
from threnda.app.models.exposure import Exposure
from threnda.app.models.exposure_history import ExposureHistory
from threnda.app.models.finding import Finding
from threnda.app.models.import_run import ImportRun
from threnda.app.models.mixins import utcnow
from threnda.app.services.dedup.correlation import correlate_new_finding
from threnda.app.services.ingestion.base import (
    DuplicateIdentityError,
    NormalizedAsset,
    NormalizedFinding,
    ParsedImport,
)
from threnda.app.services.risk_scoring import recompute_and_persist_risk_score


def compute_file_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def find_duplicate_source_finding_ids(findings: tuple[NormalizedFinding, ...]) -> list[str]:
    """Identity values that appear more than once within one parsed import.

    Two records sharing a ``source_finding_id`` inside the *same* upload
    would otherwise both try to become "the" row for that identity and
    collide on ``uq_findings_workspace_source_finding_id`` on the second
    insert — the differential engine only tracks state *between* imports,
    not duplicates *within* one. Returned in first-seen order, deduplicated,
    for a readable error message.
    """
    seen: set[str] = set()
    duplicates: list[str] = []
    for finding in findings:
        if finding.source_finding_id in seen and finding.source_finding_id not in duplicates:
            duplicates.append(finding.source_finding_id)
        seen.add(finding.source_finding_id)
    return duplicates


def _content_changed(finding: Finding, normalized: NormalizedFinding) -> bool:
    return (
        finding.title != normalized.title
        or finding.description != normalized.description
        or finding.severity != normalized.severity
        or finding.cvss_score != normalized.cvss_score
        or finding.cve != normalized.cve
        or finding.cwe != normalized.cwe
        or finding.evidence != normalized.evidence
        or finding.remediation != normalized.remediation
        or finding.fingerprint != normalized.fingerprint
        or finding.exploit_evidence != normalized.exploit_evidence
        or list(finding.references) != list(normalized.references)
    )


def _apply_incoming_content(
    db: Session, finding: Finding, normalized: NormalizedFinding, workspace_id: int
) -> None:
    finding.title = normalized.title
    finding.description = normalized.description
    finding.severity = normalized.severity
    finding.cvss_score = normalized.cvss_score
    finding.cve = normalized.cve
    finding.cwe = normalized.cwe
    finding.evidence = normalized.evidence
    finding.remediation = normalized.remediation
    finding.exploit_evidence = normalized.exploit_evidence
    finding.references = list(normalized.references)
    finding.raw_data = normalized.raw_data
    # `fingerprint` (the cross-source correlation signal) is allowed to
    # change with content — e.g. a title edit — precisely because it is
    # *not* the differential engine's identity key. See finding.py's
    # comment on uq_findings_workspace_source_finding_id.
    finding.fingerprint = normalized.fingerprint
    finding.asset_id = (
        _get_or_create_asset(db, workspace_id, normalized.asset).id
        if normalized.asset is not None
        else None
    )


def _get_or_create_asset(db: Session, workspace_id: int, normalized: NormalizedAsset) -> Asset:
    existing = db.execute(
        select(Asset).where(
            Asset.workspace_id == workspace_id, Asset.canonical_key == normalized.canonical_key
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.last_seen = utcnow()
        return existing

    asset = Asset(
        workspace_id=workspace_id,
        canonical_key=normalized.canonical_key,
        name=normalized.name,
        asset_type=normalized.asset_type,
    )
    db.add(asset)
    db.flush()
    return asset


def _maybe_recompute_exposure_score(db: Session, finding: Finding) -> None:
    """Recompute the linked exposure's risk score, if there is one.

    Called wherever this module changes something a linked finding
    contributes to scoring (content on UPDATED/REOPENED, active-vs-resolved
    status either way) — see threnda.app.services.risk_scoring. A finding
    with no exposure yet (still pending POSSIBLE review) has nothing to
    recompute.
    """
    if finding.exposure_id is None:
        return
    exposure = db.get(Exposure, finding.exposure_id)
    if exposure is not None:
        recompute_and_persist_risk_score(db, exposure)


def _record_lifecycle_event(
    db: Session, finding: Finding, event_type: ExposureHistoryEventType
) -> None:
    """Audit a RESOLVED/REOPENED transition against its linked exposure.

    A finding can be RESOLVED or REOPENED while still unlinked (e.g. a
    POSSIBLE-band candidate awaiting review that then disappears and
    reappears across imports) — there is no exposure to audit against yet
    in that case, so this is a no-op rather than an error. Aggregate
    ``ImportRun`` counters alone can't answer "which finding changed in
    this import" (docs/milestone-3-review.md, finding 5); this makes that
    answerable per finding via the exposure's own audit trail.
    """
    if finding.exposure_id is None:
        return
    db.add(
        ExposureHistory(
            exposure_id=finding.exposure_id,
            event_type=event_type,
            new_value=f"finding_id={finding.id}",
            actor="system",
        )
    )


def persist_parsed_import(
    db: Session,
    *,
    workspace_id: int,
    source_id: int,
    filename: str,
    raw: bytes,
    parsed: ParsedImport,
    infer_resolved: bool = True,
) -> ImportRun:
    duplicate_identities = find_duplicate_source_finding_ids(parsed.findings)
    if duplicate_identities:
        raise DuplicateIdentityError(
            "This import contains more than one record with the same identity "
            f"within this source: {duplicate_identities!r}. Each finding in a "
            "single upload must have a unique identity within its source."
        )

    now = utcnow()
    import_run = ImportRun(
        workspace_id=workspace_id,
        source_id=source_id,
        filename=filename,
        file_hash=compute_file_hash(raw),
        status=ImportStatus.RUNNING,
        total_records=parsed.total_records,
    )
    db.add(import_run)
    db.flush()

    existing_for_source = (
        db.execute(
            select(Finding).where(
                Finding.workspace_id == workspace_id, Finding.source_id == source_id
            )
        )
        .scalars()
        .all()
    )
    active_by_identity = {
        f.source_finding_id: f for f in existing_for_source if f.resolved_at is None
    }
    resolved_by_identity = {
        f.source_finding_id: f for f in existing_for_source if f.resolved_at is not None
    }

    seen_identities: set[str] = set()
    new_count = unchanged_count = updated_count = reopened_count = 0

    for normalized in parsed.findings:
        seen_identities.add(normalized.source_finding_id)

        if normalized.source_finding_id in active_by_identity:
            finding = active_by_identity[normalized.source_finding_id]
            changed = _content_changed(finding, normalized)
            _apply_incoming_content(db, finding, normalized, workspace_id)
            finding.last_seen = now
            finding.import_run_id = import_run.id
            if changed:
                updated_count += 1
                # Content that changed could be exactly the CVSS/exploit
                # evidence the linked exposure's score depends on.
                _maybe_recompute_exposure_score(db, finding)
            else:
                unchanged_count += 1
            continue

        if normalized.source_finding_id in resolved_by_identity:
            finding = resolved_by_identity[normalized.source_finding_id]
            _apply_incoming_content(db, finding, normalized, workspace_id)
            finding.resolved_at = None
            finding.last_seen = now
            finding.import_run_id = import_run.id
            _record_lifecycle_event(db, finding, ExposureHistoryEventType.REOPENED)
            reopened_count += 1
            # A reopened finding re-enters the "active" set the risk score
            # aggregates over (threnda.app.services.risk_scoring).
            _maybe_recompute_exposure_score(db, finding)
            continue

        asset_id = (
            _get_or_create_asset(db, workspace_id, normalized.asset).id
            if normalized.asset is not None
            else None
        )
        finding = Finding(
            workspace_id=workspace_id,
            source_id=source_id,
            import_run_id=import_run.id,
            asset_id=asset_id,
            fingerprint=normalized.fingerprint,
            source_finding_id=normalized.source_finding_id,
            title=normalized.title,
            description=normalized.description,
            severity=normalized.severity,
            cvss_score=normalized.cvss_score,
            cve=normalized.cve,
            cwe=normalized.cwe,
            evidence=normalized.evidence,
            remediation=normalized.remediation,
            exploit_evidence=normalized.exploit_evidence,
            references=list(normalized.references),
            raw_data=normalized.raw_data,
            first_seen=now,
            last_seen=now,
        )
        db.add(finding)
        db.flush()
        correlate_new_finding(db, finding)
        # Sets finding.exposure_id for EXACT/HIGH/standalone correlation;
        # stays None for a POSSIBLE candidate, which has no exposure yet
        # to score (see _maybe_recompute_exposure_score).
        _maybe_recompute_exposure_score(db, finding)
        new_count += 1

    # An import with row errors proves nothing about absence: a record that
    # failed to normalize never reaches `parsed.findings` at all, so its
    # identity can't be told apart from "genuinely gone" here — inferring
    # RESOLVED for every active identity this run didn't see would risk
    # falsely resolving a finding whose only problem was a malformed row
    # this time (docs/milestone-3-review.md, finding 3). Suppress the whole
    # absence-based RESOLVED step for this run; the successfully-parsed
    # records above are still fully processed either way.
    resolved_count = 0
    if infer_resolved and not parsed.errors:
        for identity, finding in active_by_identity.items():
            if identity not in seen_identities:
                finding.resolved_at = now
                _record_lifecycle_event(db, finding, ExposureHistoryEventType.RESOLVED)
                resolved_count += 1
                # This finding just left the "active" set the risk score
                # aggregates over (threnda.app.services.risk_scoring).
                _maybe_recompute_exposure_score(db, finding)

    import_run.new_findings = new_count
    import_run.unchanged_findings = unchanged_count
    import_run.updated_findings = updated_count
    import_run.reopened_findings = reopened_count
    import_run.resolved_findings = resolved_count
    import_run.successful_records = new_count + unchanged_count + updated_count + reopened_count
    import_run.failed_records = len(parsed.errors)
    import_run.status = ImportStatus.COMPLETED
    import_run.completed_at = utcnow()
    db.flush()
    return import_run
