"""Unit tests for the SARIF 2.1.0 importer.

Fixture SARIF documents are entirely synthetic, matching the fixed
structure `demo-data/sarif/synthetic-scan.sarif.json` exercises for real:
multiple runs, ruleId and ruleIndex lookup, explicit vs rule-default
severity, a physical location, a help link, partialFingerprints, and a
non-fail-kind result that must be excluded.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from threnda.app.models.enums import SeverityLevel
from threnda.app.services.ingestion.base import ImportFormatError
from threnda.app.services.ingestion.sarif_importer import SarifImporter


def _encode(document: dict[str, Any]) -> bytes:
    return json.dumps(document).encode("utf-8")


def _run(
    *,
    rules: list[dict[str, Any]] | None = None,
    results: list[dict[str, Any]] | None = None,
    tool_name: str = "SyntheticTool",
) -> dict[str, Any]:
    return {
        "tool": {"driver": {"name": tool_name, "rules": rules or []}},
        "results": results or [],
    }


def test_explicit_level_takes_priority_over_rule_default() -> None:
    document = {
        "runs": [
            _run(
                rules=[{"id": "R1", "defaultConfiguration": {"level": "warning"}}],
                results=[{"ruleId": "R1", "level": "error", "message": {"text": "T"}}],
            )
        ]
    }

    result = SarifImporter().parse(_encode(document))

    assert result.findings[0].severity is SeverityLevel.HIGH


def test_falls_back_to_rule_default_configuration_level() -> None:
    document = {
        "runs": [
            _run(
                rules=[{"id": "R1", "defaultConfiguration": {"level": "note"}}],
                results=[{"ruleId": "R1", "message": {"text": "T"}}],
            )
        ]
    }

    result = SarifImporter().parse(_encode(document))

    assert result.findings[0].severity is SeverityLevel.LOW


def test_defaults_to_warning_level_when_nothing_specifies_one() -> None:
    document = {"runs": [_run(results=[{"message": {"text": "T"}}])]}

    result = SarifImporter().parse(_encode(document))

    assert result.findings[0].severity is SeverityLevel.MEDIUM


def test_rule_index_lookup_when_rule_id_absent() -> None:
    document = {
        "runs": [
            _run(
                rules=[{"id": "R0"}, {"id": "R1", "helpUri": "https://example.test/r1"}],
                results=[{"ruleIndex": 1, "message": {"text": "T"}}],
            )
        ]
    }

    result = SarifImporter().parse(_encode(document))

    assert result.findings[0].references == ("https://example.test/r1",)


def test_non_fail_kind_results_are_excluded_without_being_errors() -> None:
    document = {
        "runs": [
            _run(
                results=[
                    {"kind": "pass", "message": {"text": "ok"}},
                    {"kind": "fail", "message": {"text": "bad"}},
                    {"kind": "notApplicable", "message": {"text": "n/a"}},
                ]
            )
        ]
    }

    result = SarifImporter().parse(_encode(document))

    assert len(result.findings) == 1
    assert result.findings[0].title == "bad"
    assert result.errors == ()


def test_kind_defaults_to_fail_when_omitted() -> None:
    document = {"runs": [_run(results=[{"message": {"text": "implicit fail"}}])]}

    result = SarifImporter().parse(_encode(document))

    assert len(result.findings) == 1


def test_fingerprint_is_stable_across_different_line_numbers() -> None:
    def make(line: int) -> dict[str, Any]:
        return {
            "runs": [
                _run(
                    results=[
                        {
                            "ruleId": "R1",
                            "message": {"text": "T"},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": "src/app.py"},
                                        "region": {"startLine": line},
                                    }
                                }
                            ],
                        }
                    ]
                )
            ]
        }

    result_a = SarifImporter().parse(_encode(make(10)))
    result_b = SarifImporter().parse(_encode(make(9999)))

    assert result_a.findings[0].fingerprint == result_b.findings[0].fingerprint


def test_partial_fingerprints_are_preserved_verbatim_in_raw_data() -> None:
    document = {
        "runs": [
            _run(
                results=[
                    {
                        "ruleId": "R1",
                        "message": {"text": "T"},
                        "partialFingerprints": {"primaryLocationLineHash": "abc123"},
                    }
                ]
            )
        ]
    }

    result = SarifImporter().parse(_encode(document))

    assert result.findings[0].raw_data["partialFingerprints"] == {
        "primaryLocationLineHash": "abc123"
    }


def test_different_partial_fingerprints_yield_different_fingerprints() -> None:
    def make(fp: str) -> dict[str, Any]:
        return {
            "runs": [
                _run(
                    results=[
                        {
                            "ruleId": "R1",
                            "message": {"text": "T"},
                            "partialFingerprints": {"primaryLocationLineHash": fp},
                        }
                    ]
                )
            ]
        }

    result_a = SarifImporter().parse(_encode(make("aaa")))
    result_b = SarifImporter().parse(_encode(make("bbb")))

    assert result_a.findings[0].fingerprint != result_b.findings[0].fingerprint


def test_multiple_runs_each_index_their_own_rules() -> None:
    document = {
        "runs": [
            _run(
                rules=[{"id": "ONLY-IN-RUN-0"}],
                results=[{"ruleIndex": 0, "message": {"text": "a"}}],
            ),
            _run(
                rules=[{"id": "ONLY-IN-RUN-1"}],
                results=[{"ruleIndex": 0, "message": {"text": "b"}}],
            ),
        ]
    }

    result = SarifImporter().parse(_encode(document))

    assert len(result.findings) == 2
    assert result.findings[0].fingerprint != result.findings[1].fingerprint


def test_missing_runs_key_raises_format_error() -> None:
    with pytest.raises(ImportFormatError):
        SarifImporter().parse(_encode({"version": "2.1.0"}))


def test_empty_runs_raises_format_error() -> None:
    with pytest.raises(ImportFormatError):
        SarifImporter().parse(_encode({"runs": []}))


def test_invalid_json_raises_format_error() -> None:
    with pytest.raises(ImportFormatError):
        SarifImporter().parse(b"not json")


def test_detect_matches_sarif_extension_and_content_heuristic() -> None:
    importer = SarifImporter()
    assert importer.detect("scan.sarif", None, b"{}")
    assert importer.detect("scan.sarif.json", None, b"{}")
    assert importer.detect(
        "report.json", None, _encode({"runs": [{"tool": {"driver": {"name": "x"}}}]})
    )
    assert not importer.detect("export.json", "application/json", b'{"findings": []}')


def test_demo_fixture_parses_end_to_end() -> None:
    from pathlib import Path

    fixture = (
        Path(__file__).resolve().parents[1] / "demo-data" / "sarif" / "synthetic-scan.sarif.json"
    )
    result = SarifImporter().parse(fixture.read_bytes())

    # 2 fail-kind results in run 0 (one explicit level, one rule-default via
    # ruleIndex) + 1 in run 1; the "pass"-kind result is excluded.
    assert len(result.findings) == 3
    assert result.errors == ()
    severities = {f.severity for f in result.findings}
    assert SeverityLevel.HIGH in severities
    assert SeverityLevel.MEDIUM in severities
