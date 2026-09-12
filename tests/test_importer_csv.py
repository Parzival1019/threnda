"""Unit tests for the CSV importer and its column mapping."""

from __future__ import annotations

import pytest

from threnda.app.models.enums import SeverityLevel
from threnda.app.services.ingestion.base import ImportFormatError
from threnda.app.services.ingestion.csv_importer import CsvImporter, guess_mapping

MAPPING = {
    "Vulnerability": "title",
    "Host": "asset",
    "Severity": "severity",
    "CVSS": "cvss",
    "CVE": "cve",
    "Description": "description",
    "Solution": "remediation",
}


def test_parses_a_valid_csv() -> None:
    raw = (
        b"Vulnerability,Host,Severity,CVSS,CVE,Description,Solution\r\n"
        b"Outdated TLS,synthetic-host.example.test,High,7.4,CVE-2099-1,Old TLS,Upgrade TLS\r\n"
    )

    result = CsvImporter(mapping=MAPPING).parse(raw)

    assert result.total_records == 1
    assert result.errors == ()
    finding = result.findings[0]
    assert finding.title == "Outdated TLS"
    assert finding.severity is SeverityLevel.HIGH
    assert finding.cvss_score == pytest.approx(7.4)
    assert finding.cve == "CVE-2099-1"
    assert finding.asset is not None
    assert finding.asset.canonical_key == "host:synthetic-host.example.test"


def test_unmapped_columns_are_ignored_not_an_error() -> None:
    """An extra column not mentioned in the mapping must not cause a
    format-level failure — only whether the *mapped* columns are usable
    matters (here, no severity/cvss is mapped, so this is a per-row error,
    not a whole-file `ImportFormatError`)."""
    raw = b"Vulnerability,ExtraColumn\r\nSomething,ignored value\r\n"

    result = CsvImporter(mapping={"Vulnerability": "title"}).parse(raw)

    assert result.total_records == 1
    assert len(result.errors) == 1
    assert "severity" in result.errors[0].message


def test_no_header_row_raises_format_error() -> None:
    with pytest.raises(ImportFormatError):
        CsvImporter(mapping=MAPPING).parse(b"")


def test_duplicate_headers_raise_format_error() -> None:
    raw = b"Vulnerability,Vulnerability\r\nA,B\r\n"
    with pytest.raises(ImportFormatError):
        CsvImporter(mapping={"Vulnerability": "title"}).parse(raw)


def test_mapped_column_missing_from_file_raises_format_error() -> None:
    raw = b"OnlyThisColumn\r\nvalue\r\n"
    with pytest.raises(ImportFormatError):
        CsvImporter(mapping=MAPPING).parse(raw)


def test_mapping_must_include_title() -> None:
    with pytest.raises(ValueError, match="title"):
        CsvImporter(mapping={"Host": "asset"})


def test_mapping_rejects_unknown_target_field() -> None:
    with pytest.raises(ValueError, match="Unknown"):
        CsvImporter(mapping={"Vulnerability": "title", "Foo": "not_a_real_field"})


def test_mapping_rejects_two_columns_to_the_same_field() -> None:
    with pytest.raises(ValueError, match="same field"):
        CsvImporter(mapping={"A": "title", "B": "title"})


def test_empty_title_cell_is_a_row_error() -> None:
    raw = b"Vulnerability,Severity\r\n,High\r\n"

    result = CsvImporter(mapping={"Vulnerability": "title", "Severity": "severity"}).parse(raw)

    assert result.findings == ()
    assert len(result.errors) == 1
    assert result.errors[0].index == 0


def test_severity_derived_from_cvss_when_label_unrecognized() -> None:
    raw = b"Vulnerability,Severity,CVSS\r\nT,not-a-severity,9.0\r\n"

    result = CsvImporter(
        mapping={"Vulnerability": "title", "Severity": "severity", "CVSS": "cvss"}
    ).parse(raw)

    assert result.findings[0].severity is SeverityLevel.CRITICAL


def test_invalid_cvss_is_a_row_error() -> None:
    raw = b"Vulnerability,CVSS\r\nT,999\r\n"

    result = CsvImporter(mapping={"Vulnerability": "title", "CVSS": "cvss"}).parse(raw)

    assert result.findings == ()
    assert len(result.errors) == 1


def test_one_bad_row_does_not_discard_a_good_one() -> None:
    raw = b"Vulnerability,Severity\r\n,High\r\nGood,Low\r\n"

    result = CsvImporter(mapping={"Vulnerability": "title", "Severity": "severity"}).parse(raw)

    assert result.total_records == 2
    assert len(result.errors) == 1
    assert len(result.findings) == 1
    assert result.findings[0].title == "Good"


def test_guess_mapping_recognizes_common_headers() -> None:
    guessed = guess_mapping(["Vulnerability", "Host", "Severity", "Unrelated Column"])

    assert guessed == {"Vulnerability": "title", "Host": "asset", "Severity": "severity"}


def test_detect_matches_csv_extension_and_content_type() -> None:
    importer = CsvImporter(mapping=MAPPING)
    assert importer.detect("export.csv", None, b"a,b\n1,2")
    assert importer.detect("export.dat", "text/csv", b"a,b\n1,2")
    assert not importer.detect("export.json", "application/json", b'{"a": 1}')
