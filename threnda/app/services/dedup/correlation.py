"""Correlation engine: decide which ``Exposure`` a new ``Finding`` belongs to.

Called exactly once per finding, at the moment it's first created (a
genuinely ``NEW`` classification from the differential engine — see
``threnda.app.services.ingestion.differential``). A finding that's merely
``UNCHANGED``/``UPDATED``/``REOPENED`` already has an ``exposure_id`` from
when it was first correlated and is never re-run through this module —
re-correlating on every re-import could move an already-reviewed finding to
a different exposure out from under a user, which nothing here should ever
do silently.

Layered per docs/community-v0.1-spec.md, "DEDUPLICATION":

- **Layer 1 (EXACT)** — another finding *already linked to an exposure*
  shares the identical fingerprint. Since the differential engine already
  handles "the same finding reappearing in the same source", a Layer-1 hit
  here means the identical fingerprint was produced by a *different*
  source — two tools independently reporting what normalizes to the exact
  same identifying signal (e.g. the same CVE against the same asset).
- **Layer 2 (fuzzy)** — deterministic title similarity
  (``threnda.app.services.dedup.similarity``) against a *candidate* set of
  existing exposures narrowed by a shared strong signal (same CVE, or same
  asset) — comparing every new finding's title against every exposure in
  the workspace would be both slow and prone to false positives from
  generically similar-sounding titles that share nothing else in common.
  Above ``HIGH_SIMILARITY_THRESHOLD`` autocorrelates (``HIGH`` confidence);
  above ``POSSIBLE_SIMILARITY_THRESHOLD`` but below that creates a
  ``PossibleDuplicate`` for manual review instead — never autocorrelated.
- **Layer 3 (semantic)** — not implemented. The spec gates it on local AI
  being configured, which Community does not require and does not enable
  by default (Milestone 6).

Confidence is recorded as a plain string in the ``ExposureHistory`` audit
entry's ``new_value`` (``"exact"``/``"high"``) rather than as its own
enum/column — it's context for a human reading the audit trail, not a
value anything queries on.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from threnda.app.models.enums import ExposureHistoryEventType
from threnda.app.models.exposure import Exposure
from threnda.app.models.exposure_history import ExposureHistory
from threnda.app.models.finding import Finding
from threnda.app.models.possible_duplicate import PossibleDuplicate
from threnda.app.services.dedup.similarity import title_similarity

HIGH_SIMILARITY_THRESHOLD = 0.92
POSSIBLE_SIMILARITY_THRESHOLD = 0.72


def correlate_new_finding(db: Session, finding: Finding, *, actor: str = "system") -> None:
    """Link ``finding`` to an exposure, or flag it for manual review.

    ``finding`` must already be flushed (have an ``id``) and must not yet
    have an ``exposure_id``. Always leaves ``finding`` linked to exactly one
    exposure by the time it returns, *unless* a ``POSSIBLE`` match was found
    — in that case a ``PossibleDuplicate`` is recorded and the finding is
    left unlinked until a human resolves it.
    """
    exact_match = db.execute(
        select(Finding)
        .where(
            Finding.workspace_id == finding.workspace_id,
            Finding.fingerprint == finding.fingerprint,
            Finding.id != finding.id,
            Finding.exposure_id.is_not(None),
        )
        .limit(1)
    ).scalar_one_or_none()
    if exact_match is not None and exact_match.exposure_id is not None:
        link_finding_to_exposure(
            db, finding, exact_match.exposure_id, confidence="exact", actor=actor
        )
        return

    best_exposure_id, best_score = _best_fuzzy_candidate(db, finding)

    if best_exposure_id is not None and best_score >= HIGH_SIMILARITY_THRESHOLD:
        link_finding_to_exposure(db, finding, best_exposure_id, confidence="high", actor=actor)
        return

    if best_exposure_id is not None and best_score >= POSSIBLE_SIMILARITY_THRESHOLD:
        db.add(
            PossibleDuplicate(
                workspace_id=finding.workspace_id,
                finding_id=finding.id,
                candidate_exposure_id=best_exposure_id,
                similarity_score=best_score,
            )
        )
        db.flush()
        return

    create_standalone_exposure(db, finding)


def _best_fuzzy_candidate(db: Session, finding: Finding) -> tuple[int | None, float]:
    candidates = _candidate_exposures(db, finding)
    best_id: int | None = None
    best_score = 0.0
    for exposure in candidates:
        score = title_similarity(finding.title, exposure.title)
        if score > best_score:
            best_score = score
            best_id = exposure.id
    return best_id, best_score


def _candidate_exposures(db: Session, finding: Finding) -> list[Exposure]:
    """Exposures sharing a strong signal (CVE or asset) with ``finding``.

    Deliberately narrow: a full-workspace title scan would be both slow at
    scale and prone to matching two findings whose titles merely sound
    alike but share nothing real in common.
    """
    signal_conditions = []
    if finding.cve:
        signal_conditions.append(
            Exposure.id.in_(
                select(Finding.exposure_id).where(
                    Finding.workspace_id == finding.workspace_id,
                    Finding.cve == finding.cve,
                    Finding.exposure_id.is_not(None),
                )
            )
        )
    if finding.asset_id is not None:
        signal_conditions.append(
            Exposure.id.in_(
                select(Finding.exposure_id).where(
                    Finding.workspace_id == finding.workspace_id,
                    Finding.asset_id == finding.asset_id,
                    Finding.exposure_id.is_not(None),
                )
            )
        )
    if not signal_conditions:
        return []

    stmt = select(Exposure).where(
        Exposure.workspace_id == finding.workspace_id, or_(*signal_conditions)
    )
    return list(db.execute(stmt).scalars().all())


def link_finding_to_exposure(
    db: Session, finding: Finding, exposure_id: int, *, confidence: str, actor: str
) -> None:
    """Link ``finding`` to an existing exposure and audit it.

    Public: also used by the possible-duplicate review API's "merge" action
    (``confidence="possible-merged"``), where a human is doing explicitly
    what this module never does automatically for a ``POSSIBLE`` match.
    """
    finding.exposure_id = exposure_id
    exposure = db.get(Exposure, exposure_id)
    if exposure is None:
        raise RuntimeError(f"correlate: exposure_id={exposure_id} does not exist")
    if finding.last_seen > exposure.last_seen:
        exposure.last_seen = finding.last_seen
    db.add(
        ExposureHistory(
            exposure_id=exposure_id,
            event_type=ExposureHistoryEventType.FINDING_CORRELATED,
            previous_value=None,
            new_value=f"finding_id={finding.id};confidence={confidence}",
            actor=actor,
        )
    )
    db.flush()


def create_standalone_exposure(db: Session, finding: Finding) -> Exposure:
    """Create a new ``Exposure`` for ``finding`` and link it.

    Used both when correlation finds no match at all, and by the
    possible-duplicate review API's "keep separate" action (see
    ``threnda.app.api.v1.possible_duplicates``) — a finding a human decided
    is *not* a duplicate still needs an exposure to appear in the normal
    queue, exactly as if no candidate had ever been found for it.
    """
    exposure = Exposure(
        workspace_id=finding.workspace_id,
        canonical_fingerprint=finding.fingerprint,
        title=finding.title,
        description=finding.description,
        severity=finding.severity,
        first_seen=finding.first_seen,
        last_seen=finding.last_seen,
    )
    db.add(exposure)
    db.flush()
    finding.exposure_id = exposure.id
    return exposure
