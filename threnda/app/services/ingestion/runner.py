"""Persist a parsed import as an ``ImportRun`` plus its ``Finding`` rows.

Transaction semantics (see docs/importers.md for the full contract):

- If the uploaded content cannot be parsed as the declared format at all,
  the caller (``threnda.app.api.v1.imports``) records a ``FAILED``
  ``ImportRun`` itself — this module is never reached for that case.
- Otherwise, ``persist_parsed_import`` always fully applies whatever the
  importer *did* manage to normalize: every successfully-parsed finding is
  written, individually-invalid records are counted in ``failed_records``
  (never silently dropped from the summary), and the ``ImportRun`` is
  marked ``COMPLETED`` with accurate counts — even when some records
  failed. Nothing here reports success for a record that wasn't actually
  persisted.
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
from threnda.app.services.ingestion.base import NormalizedAsset, ParsedImport


def compute_file_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def persist_parsed_import(
    db: Session,
    *,
    workspace_id: int,
    source_id: int,
    filename: str,
    raw: bytes,
    parsed: ParsedImport,
) -> ImportRun:
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

    for finding in parsed.findings:
        asset_id = None
        if finding.asset is not None:
            asset_id = _get_or_create_asset(db, workspace_id, finding.asset).id

        db.add(
            Finding(
                workspace_id=workspace_id,
                source_id=source_id,
                import_run_id=import_run.id,
                asset_id=asset_id,
                fingerprint=finding.fingerprint,
                source_finding_id=finding.source_finding_id,
                title=finding.title,
                description=finding.description,
                severity=finding.severity,
                cvss_score=finding.cvss_score,
                cve=finding.cve,
                cwe=finding.cwe,
                evidence=finding.evidence,
                remediation=finding.remediation,
                references=list(finding.references),
                raw_data=finding.raw_data,
            )
        )

    import_run.successful_records = len(parsed.findings)
    import_run.failed_records = len(parsed.errors)
    import_run.status = ImportStatus.COMPLETED
    import_run.completed_at = utcnow()
    db.flush()
    return import_run


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
