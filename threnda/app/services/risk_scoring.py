"""Deterministic, explainable, rule-based risk scoring (Milestone 4).

See docs/risk-scoring.md for the full point table and rationale, and
docs/community-v0.1-spec.md, "RISK SCORING": severity, CVSS, asset
environment, internet exposure (if known), exploit evidence (if known),
finding age, and manual business criticality. Explicitly **not** AI-based
— the spec forbids that for the authoritative score, and a fixed rule
table is what "explainable" and "configurable... without a rewrite" mean
here: every contribution is a named, documented, independently-testable
rule, and the whole point table lives in one validated ``RiskScoringConfig``
dataclass a future user-configuration UI could construct differently
without touching the engine itself.

Persistence: ``Exposure.risk_score`` is recomputed and written by
``recompute_and_persist_risk_score`` at every point new signal can enter —
exposure creation, a finding being linked, a linked finding's content
changing on re-import, or a linked asset's manual fields changing (see the
call sites in ``threnda.app.services.dedup.correlation``,
``threnda.app.services.ingestion.runner`` and this module's
``recompute_scores_for_asset``). ``GET /api/v1/exposures/{id}`` instead
calls ``compute_risk_score`` directly and returns that fresh result without
persisting it, so the single-item view (and its breakdown) is always live.
The one gap this leaves — the age component drifting between recomputes,
since Community has no background worker — is a documented limitation, not
a silent inaccuracy: every *event* that could change the score does
retrigger a recompute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.enums import AssetCriticality, AssetEnvironment, SeverityLevel
from threnda.app.models.exposure import Exposure
from threnda.app.models.finding import Finding
from threnda.app.models.mixins import utcnow

_MIN_SCORE = 0
_MAX_SCORE = 100
_MIN_POINTS = -100
_MAX_POINTS = 100


@dataclass(frozen=True)
class RiskScoringConfig:
    """The full, validated point table the scoring engine runs against.

    Every mapping must have an entry for every member of the enum it keys
    on — a config missing e.g. ``SeverityLevel.INFORMATIONAL`` is rejected
    at construction time rather than silently scoring those exposures as
    0-for-that-rule with no way to tell "deliberately zero" from "forgot
    this one". This is what lets a later user-supplied configuration reuse
    this exact class (and these exact checks) instead of a parallel,
    unvalidated path.
    """

    severity_points: dict[SeverityLevel, int]
    cvss_high_threshold: float
    cvss_high_points: int
    cvss_medium_threshold: float
    cvss_medium_points: int
    cvss_low_threshold: float
    cvss_low_points: int
    environment_points: dict[AssetEnvironment, int]
    internet_facing_true_points: int
    internet_facing_false_points: int
    exploit_evidence_true_points: int
    exploit_evidence_false_points: int
    criticality_points: dict[AssetCriticality, int]
    # (minimum_age_days, points), strictly ascending by minimum_age_days,
    # first entry's minimum must be 0. The points for an exposure's age is
    # the last bucket whose minimum it has reached or passed.
    age_buckets_days: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        for enum_cls, mapping, name in (
            (SeverityLevel, self.severity_points, "severity_points"),
            (AssetEnvironment, self.environment_points, "environment_points"),
            (AssetCriticality, self.criticality_points, "criticality_points"),
        ):
            missing = set(enum_cls) - set(mapping)
            if missing:
                raise ValueError(
                    f"{name} is missing entries for: {sorted(m.value for m in missing)}"
                )
            self._validate_points(mapping.values(), name)

        self._validate_points(
            [
                self.cvss_high_points,
                self.cvss_medium_points,
                self.cvss_low_points,
                self.internet_facing_true_points,
                self.internet_facing_false_points,
                self.exploit_evidence_true_points,
                self.exploit_evidence_false_points,
            ],
            "scalar point fields",
        )

        if not (
            0.0
            <= self.cvss_low_threshold
            <= self.cvss_medium_threshold
            <= self.cvss_high_threshold
            <= 10.0
        ):
            raise ValueError(
                "cvss thresholds must satisfy "
                "0 <= cvss_low_threshold <= cvss_medium_threshold <= cvss_high_threshold <= 10"
            )

        if not self.age_buckets_days:
            raise ValueError("age_buckets_days must not be empty")
        if self.age_buckets_days[0][0] != 0:
            raise ValueError("age_buckets_days's first bucket must start at 0 days")
        minimums = [minimum for minimum, _ in self.age_buckets_days]
        if minimums != sorted(minimums) or len(set(minimums)) != len(minimums):
            raise ValueError("age_buckets_days must be strictly ascending by minimum_age_days")
        self._validate_points((points for _, points in self.age_buckets_days), "age_buckets_days")

    @staticmethod
    def _validate_points(values: object, name: str) -> None:
        for value in values:  # type: ignore[attr-defined]
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} must contain plain integers, got {value!r}")
            if not (_MIN_POINTS <= value <= _MAX_POINTS):
                raise ValueError(
                    f"{name} value {value} out of range [{_MIN_POINTS}, {_MAX_POINTS}]"
                )


#: Conservative, documented defaults — see docs/risk-scoring.md for the
#: reasoning behind every number here.
DEFAULT_RISK_CONFIG = RiskScoringConfig(
    severity_points={
        SeverityLevel.INFORMATIONAL: 2,
        SeverityLevel.LOW: 8,
        SeverityLevel.MEDIUM: 20,
        SeverityLevel.HIGH: 35,
        SeverityLevel.CRITICAL: 50,
    },
    cvss_high_threshold=9.0,
    cvss_high_points=15,
    cvss_medium_threshold=7.0,
    cvss_medium_points=10,
    cvss_low_threshold=4.0,
    cvss_low_points=5,
    environment_points={
        AssetEnvironment.PRODUCTION: 15,
        AssetEnvironment.STAGING: 5,
        AssetEnvironment.DEVELOPMENT: 0,
        AssetEnvironment.TEST: 0,
        AssetEnvironment.UNKNOWN: 0,
    },
    internet_facing_true_points=15,
    internet_facing_false_points=-5,
    exploit_evidence_true_points=20,
    exploit_evidence_false_points=-5,
    criticality_points={
        AssetCriticality.CRITICAL: 15,
        AssetCriticality.HIGH: 10,
        AssetCriticality.MEDIUM: 5,
        AssetCriticality.LOW: 0,
        AssetCriticality.UNKNOWN: 0,
    },
    age_buckets_days=((0, 0), (7, 5), (30, 10), (90, 15)),
)


@dataclass(frozen=True)
class RiskScoreComponent:
    """One named, explainable contribution to a risk score."""

    label: str
    points: int
    detail: str


@dataclass(frozen=True)
class RiskScoreResult:
    total: int
    components: tuple[RiskScoreComponent, ...] = field(default_factory=tuple)


def _aggregate_cvss(findings: list[Finding]) -> float | None:
    scores = [f.cvss_score for f in findings if f.cvss_score is not None]
    return max(scores) if scores else None


def _aggregate_exploit_evidence(findings: list[Finding]) -> bool | None:
    """True beats False beats unknown — one confirmed exploit is enough."""
    values = {f.exploit_evidence for f in findings if f.exploit_evidence is not None}
    if True in values:
        return True
    if False in values:
        return False
    return None


def _aggregate_environment(
    findings: list[Finding], config: RiskScoringConfig
) -> AssetEnvironment | None:
    environments = {f.asset.environment for f in findings if f.asset is not None}
    if not environments:
        return None
    return max(environments, key=lambda env: config.environment_points[env])


def _aggregate_internet_facing(findings: list[Finding]) -> bool | None:
    values = {f.asset.internet_facing for f in findings if f.asset is not None} - {None}
    if True in values:
        return True
    if False in values:
        return False
    return None


def _aggregate_criticality(
    findings: list[Finding], config: RiskScoringConfig
) -> AssetCriticality | None:
    criticalities = {f.asset.business_criticality for f in findings if f.asset is not None}
    if not criticalities:
        return None
    return max(criticalities, key=lambda c: config.criticality_points[c])


def _age_days(first_seen: datetime, now: datetime) -> int:
    return max((now - first_seen).days, 0)


def _age_points(age_days: int, config: RiskScoringConfig) -> tuple[int, int]:
    """Returns (bucket_minimum_days, points) for the last bucket reached."""
    applicable = [bucket for bucket in config.age_buckets_days if bucket[0] <= age_days]
    return applicable[-1] if applicable else config.age_buckets_days[0]


def _scoring_findings(exposure: Exposure) -> list[Finding]:
    """Findings whose content should count toward the score.

    Excludes findings the *source data* has stopped reporting
    (``resolved_at is not None`` — see docs/deduplication.md, the import
    differential engine): a finding no source currently reports shouldn't
    keep inflating an exposure's CVSS/exploit-evidence/environment/
    criticality signal. Falls back to every linked finding if all of them
    are source-resolved, so a still-open exposure never loses its severity/
    identity context entirely just because its last active finding aged out.
    """
    active = [f for f in exposure.findings if f.resolved_at is None]
    return active or list(exposure.findings)


def compute_risk_score(
    exposure: Exposure,
    *,
    config: RiskScoringConfig = DEFAULT_RISK_CONFIG,
    now: datetime | None = None,
) -> RiskScoreResult:
    """Compute (never persists) the risk score and its explainable breakdown."""
    findings = _scoring_findings(exposure)
    now = now or utcnow()
    components: list[RiskScoreComponent] = []

    severity_pts = config.severity_points[exposure.severity]
    components.append(
        RiskScoreComponent(
            label="Severity",
            points=severity_pts,
            detail=f"Exposure severity is {exposure.severity.value}",
        )
    )

    max_cvss = _aggregate_cvss(findings)
    if max_cvss is None:
        components.append(
            RiskScoreComponent(label="CVSS", points=0, detail="No CVSS score reported")
        )
    else:
        if max_cvss >= config.cvss_high_threshold:
            pts, threshold = config.cvss_high_points, config.cvss_high_threshold
        elif max_cvss >= config.cvss_medium_threshold:
            pts, threshold = config.cvss_medium_points, config.cvss_medium_threshold
        elif max_cvss >= config.cvss_low_threshold:
            pts, threshold = config.cvss_low_points, config.cvss_low_threshold
        else:
            pts, threshold = 0, None
        detail = (
            f"Highest reported CVSS is {max_cvss:g} (>= {threshold:g})"
            if threshold is not None
            else f"Highest reported CVSS is {max_cvss:g}"
        )
        components.append(RiskScoreComponent(label="CVSS", points=pts, detail=detail))

    environment = _aggregate_environment(findings, config)
    if environment is None:
        components.append(
            RiskScoreComponent(
                label="Asset environment", points=0, detail="No asset environment known"
            )
        )
    else:
        components.append(
            RiskScoreComponent(
                label="Asset environment",
                points=config.environment_points[environment],
                detail=f"Highest-risk linked asset environment is {environment.value}",
            )
        )

    internet_facing = _aggregate_internet_facing(findings)
    if internet_facing is None:
        components.append(
            RiskScoreComponent(
                label="Internet exposure", points=0, detail="Not known whether internet-facing"
            )
        )
    elif internet_facing:
        components.append(
            RiskScoreComponent(
                label="Internet exposure",
                points=config.internet_facing_true_points,
                detail="At least one linked asset is known internet-facing",
            )
        )
    else:
        components.append(
            RiskScoreComponent(
                label="Internet exposure",
                points=config.internet_facing_false_points,
                detail="Linked asset(s) are known not internet-facing",
            )
        )

    exploit_evidence = _aggregate_exploit_evidence(findings)
    if exploit_evidence is None:
        components.append(
            RiskScoreComponent(
                label="Exploit evidence", points=0, detail="Not known whether exploited"
            )
        )
    elif exploit_evidence:
        components.append(
            RiskScoreComponent(
                label="Exploit evidence",
                points=config.exploit_evidence_true_points,
                detail="At least one linked finding reports known exploit evidence",
            )
        )
    else:
        components.append(
            RiskScoreComponent(
                label="Exploit evidence",
                points=config.exploit_evidence_false_points,
                detail="Linked finding(s) explicitly report no known exploit evidence",
            )
        )

    criticality = _aggregate_criticality(findings, config)
    if criticality is None or criticality is AssetCriticality.UNKNOWN:
        components.append(
            RiskScoreComponent(
                label="Business criticality", points=0, detail="No business criticality set"
            )
        )
    else:
        components.append(
            RiskScoreComponent(
                label="Business criticality",
                points=config.criticality_points[criticality],
                detail=f"Highest-criticality linked asset is {criticality.value}",
            )
        )

    age_days = _age_days(exposure.first_seen, now)
    _bucket_min, age_pts = _age_points(age_days, config)
    components.append(
        RiskScoreComponent(
            label="Finding age", points=age_pts, detail=f"Open for {age_days} day(s)"
        )
    )

    raw_total = sum(c.points for c in components)
    total = max(_MIN_SCORE, min(_MAX_SCORE, raw_total))
    return RiskScoreResult(total=total, components=tuple(components))


def recompute_and_persist_risk_score(
    db: Session, exposure: Exposure, *, config: RiskScoringConfig = DEFAULT_RISK_CONFIG
) -> RiskScoreResult:
    """Compute and write ``exposure.risk_score`` — the list/sort-facing value."""
    result = compute_risk_score(exposure, config=config)
    exposure.risk_score = result.total
    db.flush()
    return result


def recompute_scores_for_asset(
    db: Session,
    asset_id: int,
    workspace_id: int,
    *,
    config: RiskScoringConfig = DEFAULT_RISK_CONFIG,
) -> list[Exposure]:
    """Recompute every exposure that has a finding linked to this asset.

    Called after a manual asset edit (business criticality / internet
    exposure) — those fields can move the score of every exposure any of
    the asset's findings are linked to, not just one.
    """
    exposure_ids = (
        db.execute(
            select(Finding.exposure_id)
            .where(
                Finding.workspace_id == workspace_id,
                Finding.asset_id == asset_id,
                Finding.exposure_id.is_not(None),
            )
            .distinct()
        )
        .scalars()
        .all()
    )

    exposures = []
    for exposure_id in exposure_ids:
        exposure = db.get(Exposure, exposure_id)
        if exposure is not None:
            recompute_and_persist_risk_score(db, exposure, config=config)
            exposures.append(exposure)
    return exposures
