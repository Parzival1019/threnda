"""Manual finding entry — a pentester types a finding in directly.

A manual finding is stored exactly like an imported one (a ``Finding`` on a
per-project "Manual entry" source, linked to its own ``Exposure``), so the
queue, history, export, report and risk score treat both identically. It
is never correlated with imported findings — see docs/finding-lifecycle.md.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.enums import (
    AssetType,
    ExposureHistoryEventType,
    SeverityLevel,
    SourceType,
)
from threnda.app.models.exposure import Exposure
from threnda.app.models.exposure_history import ExposureHistory
from threnda.app.models.finding import Finding
from threnda.app.models.mixins import utcnow
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace
from threnda.app.services.dedup.correlation import create_standalone_exposure
from threnda.app.services.exposures import ExposureChanges, apply_exposure_update
from threnda.app.services.ingestion.base import NormalizedAsset
from threnda.app.services.ingestion.fingerprint import compute_fingerprint
from threnda.app.services.ingestion.runner import get_or_create_asset
from threnda.app.services.risk_scoring import recompute_and_persist_risk_score

MANUAL_SOURCE_NAME = "Manual entry"


@dataclass(frozen=True)
class ManualFindingInput:
    title: str
    severity: SeverityLevel
    description: str | None = None
    affected_system: str | None = None
    remediation: str | None = None
    owner: str | None = None
    due_date: date | None = None


def asset_for_system(name: str) -> NormalizedAsset:
    """The asset identity used for an "affected system" typed by a person.

    Shared with the findings-CSV template importer so a manual finding and an
    imported one on the same system land on the same ``Asset``.
    """
    clean = name.strip()
    return NormalizedAsset(
        canonical_key=f"{AssetType.HOST.value}:{clean.lower()}",
        name=clean,
        asset_type=AssetType.HOST,
    )


def get_or_create_source(
    db: Session, workspace: Workspace, *, name: str, source_type: SourceType
) -> Source:
    source = db.execute(
        select(Source).where(
            Source.workspace_id == workspace.id,
            Source.name == name,
            Source.source_type == source_type,
        )
    ).scalar_one_or_none()
    if source is None:
        source = Source(workspace_id=workspace.id, name=name, source_type=source_type)
        db.add(source)
        db.flush()
    return source


def create_manual_finding(
    db: Session, workspace: Workspace, data: ManualFindingInput, *, actor: str
) -> Exposure:
    title = data.title.strip()
    if not title:
        raise ValueError("title cannot be empty")
    if len(title) > 500:
        raise ValueError("title can be at most 500 characters")
    system = (data.affected_system or "").strip() or None

    source = get_or_create_source(
        db, workspace, name=MANUAL_SOURCE_NAME, source_type=SourceType.MANUAL
    )
    asset = get_or_create_asset(db, workspace.id, asset_for_system(system)) if system else None
    now = utcnow()
    finding = Finding(
        workspace_id=workspace.id,
        source_id=source.id,
        asset_id=asset.id if asset else None,
        fingerprint=compute_fingerprint(None, asset.canonical_key if asset else None, title),
        source_finding_id=f"manual-{uuid.uuid4().hex}",
        title=title,
        description=(data.description or "").strip() or None,
        severity=data.severity,
        remediation=(data.remediation or "").strip() or None,
        references=[],
        raw_data={},
        first_seen=now,
        last_seen=now,
    )
    db.add(finding)
    db.flush()
    exposure = create_standalone_exposure(db, finding)
    exposure.affected_system = system
    exposure.remediation = finding.remediation
    db.add(
        ExposureHistory(
            exposure=exposure,
            event_type=ExposureHistoryEventType.CREATED,
            new_value="manual entry",
            actor=actor,
        )
    )
    changes: ExposureChanges = {}
    if data.owner and data.owner.strip():
        changes["owner"] = data.owner.strip()[:200]
    if data.due_date is not None:
        changes["remediation_target_date"] = data.due_date
    if changes:
        apply_exposure_update(db, exposure, changes, note=None, actor=actor)
    recompute_and_persist_risk_score(db, exposure)
    return exposure
