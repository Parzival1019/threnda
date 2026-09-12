"""Unit tests for the deterministic risk-scoring engine.

Every scoring input is exercised in isolation (all other inputs held at
their "unknown"/neutral value) plus the clamping boundaries and the
config's own validation — see docs/risk-scoring.md for the point table
these numbers are pinned against.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from threnda.app.models.asset import Asset
from threnda.app.models.enums import AssetCriticality, AssetEnvironment, SeverityLevel
from threnda.app.models.exposure import Exposure
from threnda.app.models.finding import Finding
from threnda.app.models.mixins import utcnow
from threnda.app.services.risk_scoring import (
    DEFAULT_RISK_CONFIG,
    RiskScoringConfig,
    compute_risk_score,
)

NOW = utcnow()


def _exposure(
    *,
    severity: SeverityLevel = SeverityLevel.INFORMATIONAL,
    first_seen: datetime | None = None,
    findings: list[Finding] | None = None,
) -> Exposure:
    exposure = Exposure(
        id=1,
        workspace_id=1,
        canonical_fingerprint="fp",
        title="synthetic exposure",
        severity=severity,
        first_seen=first_seen or NOW,
    )
    exposure.findings = findings or []
    return exposure


def _finding(**kwargs: object) -> Finding:
    defaults: dict[str, object] = {
        "id": 1,
        "workspace_id": 1,
        "source_id": 1,
        "fingerprint": "fp",
        "source_finding_id": "s1",
        "title": "synthetic finding",
        "severity": SeverityLevel.INFORMATIONAL,
    }
    defaults.update(kwargs)
    return Finding(**defaults)


def _asset(**kwargs: object) -> Asset:
    # SQLAlchemy's `mapped_column(default=...)` only applies at INSERT
    # time — a plain, never-flushed instance leaves an unset column `None`
    # regardless of its configured default, so every field the scoring
    # engine reads needs an explicit value here, matching what a real
    # persisted row's default actually is.
    defaults: dict[str, object] = {
        "id": 1,
        "workspace_id": 1,
        "canonical_key": "host:a",
        "name": "a",
        "environment": AssetEnvironment.UNKNOWN,
        "internet_facing": None,
        "business_criticality": AssetCriticality.UNKNOWN,
    }
    defaults.update(kwargs)
    return Asset(**defaults)


@pytest.mark.parametrize(
    "severity,expected_points",
    [
        (SeverityLevel.INFORMATIONAL, 2),
        (SeverityLevel.LOW, 8),
        (SeverityLevel.MEDIUM, 20),
        (SeverityLevel.HIGH, 35),
        (SeverityLevel.CRITICAL, 50),
    ],
)
def test_severity_alone_matches_the_documented_table(
    severity: SeverityLevel, expected_points: int
) -> None:
    result = compute_risk_score(_exposure(severity=severity), now=NOW)
    assert result.total == expected_points


@pytest.mark.parametrize(
    "cvss,expected_bonus",
    [(9.9, 15), (9.0, 15), (8.9, 10), (7.0, 10), (6.9, 5), (4.0, 5), (3.9, 0)],
)
def test_cvss_thresholds_are_inclusive_at_the_boundary(cvss: float, expected_bonus: int) -> None:
    exposure = _exposure(findings=[_finding(cvss_score=cvss)])
    result = compute_risk_score(exposure, now=NOW)
    # Baseline (informational, everything else unknown, age 0) is 2.
    assert result.total == 2 + expected_bonus


def test_no_cvss_reported_contributes_nothing_and_says_so() -> None:
    result = compute_risk_score(_exposure(findings=[_finding()]), now=NOW)
    cvss_component = next(c for c in result.components if c.label == "CVSS")
    assert cvss_component.points == 0
    assert "no cvss" in cvss_component.detail.lower()


def test_max_cvss_across_multiple_findings_is_used() -> None:
    exposure = _exposure(
        findings=[
            _finding(id=1, source_finding_id="a", cvss_score=3.0),
            _finding(id=2, source_finding_id="b", cvss_score=9.5),
        ]
    )
    result = compute_risk_score(exposure, now=NOW)
    assert result.total == 2 + 15


@pytest.mark.parametrize(
    "environment,expected_points",
    [
        (AssetEnvironment.PRODUCTION, 15),
        (AssetEnvironment.STAGING, 5),
        (AssetEnvironment.DEVELOPMENT, 0),
        (AssetEnvironment.TEST, 0),
        (AssetEnvironment.UNKNOWN, 0),
    ],
)
def test_asset_environment_matches_the_documented_table(
    environment: AssetEnvironment, expected_points: int
) -> None:
    asset = _asset(environment=environment)
    exposure = _exposure(findings=[_finding(asset=asset)])
    result = compute_risk_score(exposure, now=NOW)
    assert result.total == 2 + expected_points


def test_worst_environment_across_assets_wins() -> None:
    exposure = _exposure(
        findings=[
            _finding(
                id=1, source_finding_id="a", asset=_asset(id=1, environment=AssetEnvironment.TEST)
            ),
            _finding(
                id=2,
                source_finding_id="b",
                asset=_asset(id=2, canonical_key="host:b", environment=AssetEnvironment.PRODUCTION),
            ),
        ]
    )
    result = compute_risk_score(exposure, now=NOW)
    assert result.total == 2 + 15


def test_internet_facing_true_and_false_and_unknown() -> None:
    unknown = compute_risk_score(_exposure(findings=[_finding(asset=_asset())]), now=NOW)
    assert unknown.total == 2

    facing = compute_risk_score(
        _exposure(findings=[_finding(asset=_asset(internet_facing=True))]), now=NOW
    )
    assert facing.total == 2 + 15

    not_facing = compute_risk_score(
        _exposure(findings=[_finding(asset=_asset(internet_facing=False))]), now=NOW
    )
    assert not_facing.total == max(0, 2 - 5)


def test_exploit_evidence_true_beats_false_across_findings() -> None:
    exposure = _exposure(
        findings=[
            _finding(id=1, source_finding_id="a", exploit_evidence=False),
            _finding(id=2, source_finding_id="b", exploit_evidence=True),
        ]
    )
    result = compute_risk_score(exposure, now=NOW)
    assert result.total == 2 + 20


def test_exploit_evidence_false_alone_is_a_small_negative() -> None:
    result = compute_risk_score(_exposure(findings=[_finding(exploit_evidence=False)]), now=NOW)
    assert result.total == max(0, 2 - 5)


@pytest.mark.parametrize(
    "criticality,expected_points",
    [
        (AssetCriticality.CRITICAL, 15),
        (AssetCriticality.HIGH, 10),
        (AssetCriticality.MEDIUM, 5),
        (AssetCriticality.LOW, 0),
        (AssetCriticality.UNKNOWN, 0),
    ],
)
def test_business_criticality_matches_the_documented_table(
    criticality: AssetCriticality, expected_points: int
) -> None:
    asset = _asset(business_criticality=criticality)
    result = compute_risk_score(_exposure(findings=[_finding(asset=asset)]), now=NOW)
    assert result.total == 2 + expected_points


@pytest.mark.parametrize(
    "age_days,expected_points",
    [(0, 0), (6, 0), (7, 5), (29, 5), (30, 10), (89, 10), (90, 15), (365, 15)],
)
def test_age_buckets_match_the_documented_table(age_days: int, expected_points: int) -> None:
    exposure = _exposure(first_seen=NOW - timedelta(days=age_days))
    result = compute_risk_score(exposure, now=NOW)
    assert result.total == 2 + expected_points


def test_score_never_exceeds_100_even_with_every_signal_maxed() -> None:
    asset = _asset(
        environment=AssetEnvironment.PRODUCTION,
        internet_facing=True,
        business_criticality=AssetCriticality.CRITICAL,
    )
    finding = _finding(cvss_score=10.0, exploit_evidence=True, asset=asset)
    exposure = _exposure(
        severity=SeverityLevel.CRITICAL, first_seen=NOW - timedelta(days=365), findings=[finding]
    )
    result = compute_risk_score(exposure, now=NOW)
    assert result.total == 100
    assert sum(c.points for c in result.components) > 100  # the clamp, not the raw sum


def test_score_never_goes_below_0_even_with_every_negative_signal() -> None:
    asset = _asset(internet_facing=False)
    finding = _finding(exploit_evidence=False, asset=asset)
    exposure = _exposure(severity=SeverityLevel.INFORMATIONAL, findings=[finding])
    result = compute_risk_score(exposure, now=NOW)
    assert result.total == 0


def test_resolved_findings_are_excluded_from_aggregation() -> None:
    """A finding the source no longer reports shouldn't keep inflating the score."""
    resolved = _finding(cvss_score=9.9, exploit_evidence=True, resolved_at=NOW)
    exposure = _exposure(findings=[resolved])
    result = compute_risk_score(exposure, now=NOW)
    # Falls back to the (sole, resolved) finding since there is no active one.
    assert result.total == 2 + 15 + 20


def test_active_finding_signal_is_used_over_a_resolved_one() -> None:
    resolved = _finding(id=1, source_finding_id="a", cvss_score=9.9, resolved_at=NOW)
    active = _finding(id=2, source_finding_id="b", cvss_score=None)
    exposure = _exposure(findings=[resolved, active])
    result = compute_risk_score(exposure, now=NOW)
    cvss_component = next(c for c in result.components if c.label == "CVSS")
    assert cvss_component.points == 0  # the active finding has no CVSS; the resolved one is ignored


def test_no_findings_at_all_still_scores_on_severity_and_age() -> None:
    result = compute_risk_score(_exposure(severity=SeverityLevel.HIGH), now=NOW)
    assert result.total == 35


def test_config_rejects_incomplete_severity_points() -> None:
    with pytest.raises(ValueError, match="severity_points"):
        RiskScoringConfig(
            **{**DEFAULT_RISK_CONFIG.__dict__, "severity_points": {SeverityLevel.CRITICAL: 50}}
        )


def test_config_rejects_out_of_range_points() -> None:
    with pytest.raises(ValueError, match="out of range"):
        RiskScoringConfig(**{**DEFAULT_RISK_CONFIG.__dict__, "cvss_high_points": 1000})


def test_config_rejects_non_integer_points() -> None:
    with pytest.raises(ValueError, match="plain integers"):
        RiskScoringConfig(**{**DEFAULT_RISK_CONFIG.__dict__, "cvss_high_points": 5.5})


def test_config_rejects_misordered_cvss_thresholds() -> None:
    with pytest.raises(ValueError, match="cvss thresholds"):
        RiskScoringConfig(
            **{
                **DEFAULT_RISK_CONFIG.__dict__,
                "cvss_high_threshold": 1.0,
                "cvss_medium_threshold": 9.0,
            }
        )


def test_config_rejects_age_buckets_not_starting_at_zero() -> None:
    with pytest.raises(ValueError, match="start at 0"):
        RiskScoringConfig(**{**DEFAULT_RISK_CONFIG.__dict__, "age_buckets_days": ((1, 0), (7, 5))})


def test_config_rejects_non_ascending_age_buckets() -> None:
    with pytest.raises(ValueError, match="ascending"):
        RiskScoringConfig(
            **{**DEFAULT_RISK_CONFIG.__dict__, "age_buckets_days": ((0, 0), (7, 5), (3, 10))}
        )


def test_default_config_is_valid() -> None:
    # Construction alone runs __post_init__; this just documents the intent.
    assert DEFAULT_RISK_CONFIG.severity_points[SeverityLevel.CRITICAL] == 50


def test_compute_is_pure_and_deterministic() -> None:
    exposure = _exposure(findings=[_finding(cvss_score=7.5)])
    first = compute_risk_score(exposure, now=NOW)
    second = compute_risk_score(exposure, now=NOW)
    assert first == second
