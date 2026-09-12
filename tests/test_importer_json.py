"""Unit tests for the generic JSON importer."""

from __future__ import annotations

import json

import pytest

from threnda.app.models.enums import AssetType, SeverityLevel
from threnda.app.services.ingestion.base import ImportFormatError
from threnda.app.services.ingestion.json_importer import JsonImporter


def _encode(document: dict) -> bytes:
    return json.dumps(document).encode("utf-8")


def test_parses_a_valid_document() -> None:
    raw = _encode(
        {
            "findings": [
                {
                    "id": "f1",
                    "title": "Synthetic finding",
                    "severity": "high",
                    "cve": "CVE-2099-00001",
                    "asset": {
                        "key": "host:synthetic.example.test",
                        "name": "synthetic.example.test",
                        "type": "host",
                    },
                    "references": ["https://example.test/advisory"],
                }
            ]
        }
    )

    result = JsonImporter().parse(raw)

    assert result.total_records == 1
    assert result.errors == ()
    finding = result.findings[0]
    assert finding.source_finding_id == "f1"
    assert finding.title == "Synthetic finding"
    assert finding.severity is SeverityLevel.HIGH
    assert finding.asset is not None
    assert finding.asset.canonical_key == "host:synthetic.example.test"
    assert finding.asset.asset_type is AssetType.HOST
    assert finding.references == ("https://example.test/advisory",)


def test_severity_derived_from_cvss_when_missing() -> None:
    raw = _encode({"findings": [{"id": "f1", "title": "T", "cvss": 9.5}]})

    result = JsonImporter().parse(raw)

    assert result.findings[0].severity is SeverityLevel.CRITICAL


def test_invalid_json_syntax_raises_format_error() -> None:
    with pytest.raises(ImportFormatError):
        JsonImporter().parse(b"{not valid json")


def test_missing_findings_key_raises_format_error() -> None:
    with pytest.raises(ImportFormatError):
        JsonImporter().parse(_encode({"not_findings": []}))


def test_findings_must_be_a_list() -> None:
    with pytest.raises(ImportFormatError):
        JsonImporter().parse(_encode({"findings": "nope"}))


def test_missing_title_is_a_row_error_not_a_format_error() -> None:
    raw = _encode({"findings": [{"id": "f1", "severity": "low"}]})

    result = JsonImporter().parse(raw)

    assert result.findings == ()
    assert result.total_records == 1
    assert len(result.errors) == 1
    assert "title" in result.errors[0].message


def test_missing_severity_and_cvss_is_a_row_error() -> None:
    raw = _encode({"findings": [{"id": "f1", "title": "T"}]})

    result = JsonImporter().parse(raw)

    assert result.errors[0].index == 0
    assert "severity" in result.errors[0].message


def test_invalid_cvss_is_a_row_error() -> None:
    raw = _encode({"findings": [{"id": "f1", "title": "T", "cvss": 99}]})

    result = JsonImporter().parse(raw)

    assert len(result.errors) == 1
    assert result.findings == ()


def test_one_bad_row_does_not_discard_a_good_one() -> None:
    raw = _encode(
        {
            "findings": [
                {"id": "bad", "title": "Bad"},
                {"id": "good", "title": "Good", "severity": "low"},
            ]
        }
    )

    result = JsonImporter().parse(raw)

    assert result.total_records == 2
    assert len(result.errors) == 1
    assert len(result.findings) == 1
    assert result.findings[0].source_finding_id == "good"


def test_detect_matches_json_extension_and_content_type() -> None:
    importer = JsonImporter()
    assert importer.detect("export.json", None, b"{}")
    assert importer.detect("export.dat", "application/json", b"{}")
    assert importer.detect("export.dat", None, b'{"findings": []}')
    assert not importer.detect("export.csv", "text/csv", b"a,b\n1,2")


def test_asset_requires_a_key() -> None:
    raw = _encode(
        {"findings": [{"id": "f1", "title": "T", "severity": "low", "asset": {"name": "x"}}]}
    )

    result = JsonImporter().parse(raw)

    assert len(result.errors) == 1
