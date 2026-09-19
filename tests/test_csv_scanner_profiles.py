"""Contract tests for the ten built-in scanner CSV profiles."""

from __future__ import annotations

from pathlib import Path

import pytest

from threnda.app.models.enums import AssetType, SeverityLevel
from threnda.app.services.ingestion.csv_importer import CsvImporter
from threnda.app.services.ingestion.csv_profiles import (
    SCANNER_CSV_PROFILES,
    CsvProfileMatch,
    detect_csv_profile,
)

FIXTURE_DIR = Path(__file__).parent.parent / "demo-data" / "csv" / "scanners"

EXPECTED = {
    "acunetix": ("SQL injection", SeverityLevel.CRITICAL, AssetType.URL),
    "burp-suite-dast": ("Cross-site scripting", SeverityLevel.HIGH, AssetType.URL),
    "checkmarx-one": ("SQL Injection", SeverityLevel.HIGH, AssetType.REPOSITORY),
    "gitlab-vulnerability-report": (
        "Hard-coded secret",
        SeverityLevel.CRITICAL,
        AssetType.REPOSITORY,
    ),
    "greenbone-openvas": ("Outdated TLS service", SeverityLevel.HIGH, AssetType.IP),
    "nessus": ("Unsupported web server", SeverityLevel.MEDIUM, AssetType.HOST),
    "qualys-vmdr": ("Synthetic remote code execution", SeverityLevel.CRITICAL, AssetType.IP),
    "rapid7-insightvm": ("Synthetic package vulnerability", SeverityLevel.CRITICAL, AssetType.IP),
    "red-hat-acs": ("CVE-2099-1011", SeverityLevel.CRITICAL, AssetType.CONTAINER_IMAGE),
    "snyk-issues": ("Prototype Pollution", SeverityLevel.HIGH, AssetType.REPOSITORY),
    "tenable-vm": ("Synthetic vulnerable service", SeverityLevel.HIGH, AssetType.HOST),
}


def _importer(match: CsvProfileMatch) -> CsvImporter:
    return CsvImporter(
        mapping=match.mapping,
        header_row_index=match.header_row_index,
        asset_type=match.profile.asset_type,
        severity_normalizer=match.profile.normalize_severity,
        field_copies=dict(match.profile.field_copies),
        profile_slug=match.profile.slug,
        profile_name=match.profile.name,
    )


def test_exactly_eleven_unique_profiles_are_registered() -> None:
    assert len(SCANNER_CSV_PROFILES) == 11
    assert len({profile.slug for profile in SCANNER_CSV_PROFILES}) == 11
    assert set(EXPECTED) == {profile.slug for profile in SCANNER_CSV_PROFILES}


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_profile_detects_and_imports_its_synthetic_export(slug: str) -> None:
    raw = (FIXTURE_DIR / f"{slug}.csv").read_bytes()

    match = detect_csv_profile(raw)

    assert match is not None
    assert match.profile.slug == slug
    result = _importer(match).parse(raw)
    assert result.errors == ()
    assert result.total_records == 1
    finding = result.findings[0]
    expected_title, expected_severity, expected_asset_type = EXPECTED[slug]
    assert finding.title == expected_title
    assert finding.severity is expected_severity
    assert finding.asset is not None
    assert finding.asset.asset_type is expected_asset_type
    assert finding.asset.canonical_key.startswith(f"{expected_asset_type.value}:")
    if slug == "red-hat-acs":
        assert finding.cve == "CVE-2099-1011"


def test_qualys_profile_finds_header_after_report_preamble() -> None:
    raw = (FIXTURE_DIR / "qualys-vmdr.csv").read_bytes()

    match = detect_csv_profile(raw)

    assert match is not None
    assert match.header_row_index == 4
    result = _importer(match).parse(raw)
    assert result.total_records == 1


def test_unknown_csv_is_not_silently_claimed_by_a_profile() -> None:
    raw = b"Finding,Host,Severity\nSynthetic issue,host.example.test,high\n"

    assert detect_csv_profile(raw) is None
