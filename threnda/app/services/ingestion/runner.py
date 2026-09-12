"""Persist a parsed import: the Milestone 3 differential engine.

Classifies every incoming normalized finding against the existing state of
its ``(workspace, source)`` — see docs/community-v0.1-spec.md, "IMPORT
DIFFERENTIALS" — and applies exactly one of:

- **NEW** — no existing finding shares this fingerprint at all: create it,
  and run it through the correlation engine
  (``threnda.app.services.dedup.correlation``) to decide which ``Exposure``
  it belongs to.
- **UNCHANGED** / **UPDATED** — an *active* (not yet resolved) finding
  already has this fingerprint: update its content in place if it
  differs, refresh ``last_seen``. Never re-correlated — it already has
  whatever ``exposure_id`` it was given when first created.
- **REOPENED** — a *resolved* finding has this fingerprint: clear
  ``resolved_at``, update content, refresh ``last_seen``. Still never
  re-correlated, for the same reason.
- **RESOLVED** — an active finding from a *previous* import of this source
  was not present in this one: set ``resolved_at``. The row is never
  deleted, and nothing about its ``Exposure`` is touched automatically —
  see docs/deduplication.md for why (this is a deliberate Community/
  Enterprise boundary, not an oversight).

Every successfully-parsed record lands in exactly one of NEW/UNCHANGED/
UPDATED/REOPENED; the resulting counts, plus RESOLVED, are stored on the
``ImportRun`` itself — that's "store this history" for the differential
engine's purposes. Individually-invalid records (already filtered out by
the importer into ``ParsedImport.errors``) are counted in
``failed_records`` as before and never silently dropped from the summary.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.asset import Asset
from threnda.app.models.enums import ImportStatus
from threnda.app.models.finding import Finding
from threnda.app.models.import_run import ImportRun
from threnda.app.models.mixins import utcnow
from threnda.app.services.dedup.correlation import correlate_new_finding
from threnda.app.services.ingestion.base import NormalizedAsset, NormalizedFinding, ParsedImport


def compute_file_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


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
        or list(finding.references) != list(normalized.references)
    )


def _apply_incoming_content(
    db: Session, finding: Finding, normalized: NormalizedFinding, workspace_id: int
) -> None:
    finding.source_finding_id = normalized.source_finding_id
    finding.title = normalized.title
    finding.description = normalized.description
    finding.severity = normalized.severity
    finding.cvss_score = normalized.cvss_score
    finding.cve = normalized.cve
    finding.cwe = normalized.cwe
    finding.evidence = normalized.evidence
    finding.remediation = normalized.remediation
    finding.references = list(normalized.references)
    finding.raw_data = normalized.raw_data
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


def persist_parsed_import(
    db: Session,
    *,
    workspace_id: int,
    source_id: int,
    filename: str,
    raw: bytes,
    parsed: ParsedImport,
) -> ImportRun:
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
    active_by_fingerprint = {f.fingerprint: f for f in existing_for_source if f.resolved_at is None}
    resolved_by_fingerprint = {
        f.fingerprint: f for f in existing_for_source if f.resolved_at is not None
    }

    seen_fingerprints: set[str] = set()
    new_count = unchanged_count = updated_count = reopened_count = 0

    for normalized in parsed.findings:
        seen_fingerprints.add(normalized.fingerprint)

        if normalized.fingerprint in active_by_fingerprint:
            finding = active_by_fingerprint[normalized.fingerprint]
            changed = _content_changed(finding, normalized)
            _apply_incoming_content(db, finding, normalized, workspace_id)
            finding.last_seen = now
            finding.import_run_id = import_run.id
            if changed:
                updated_count += 1
            else:
                unchanged_count += 1
            continue

        if normalized.fingerprint in resolved_by_fingerprint:
            finding = resolved_by_fingerprint[normalized.fingerprint]
            _apply_incoming_content(db, finding, normalized, workspace_id)
            finding.resolved_at = None
            finding.last_seen = now
            finding.import_run_id = import_run.id
            reopened_count += 1
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
            references=list(normalized.references),
            raw_data=normalized.raw_data,
            first_seen=now,
            last_seen=now,
        )
        db.add(finding)
        db.flush()
        correlate_new_finding(db, finding)
        new_count += 1

    resolved_count = 0
    for fingerprint, finding in active_by_fingerprint.items():
        if fingerprint not in seen_fingerprints:
            finding.resolved_at = now
            resolved_count += 1

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
