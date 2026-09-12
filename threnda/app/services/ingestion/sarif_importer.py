"""SARIF 2.1.0 importer.

Interprets output against the public OASIS SARIF 2.1.0 specification
(https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html),
independent of any particular scanner:

- A run's ``results[]`` are the findings; a rule is looked up by
  ``result.ruleId`` first, falling back to ``result.ruleIndex`` into
  ``tool.driver.rules[]`` when ``ruleId`` is absent (§3.27.5).
- Only ``kind: "fail"`` results represent an actual issue (``kind``
  defaults to ``"fail"`` when omitted, per §3.27.9) — ``pass``,
  ``notApplicable``, ``review``, ``open`` and ``informational`` results are
  not security findings and are skipped, not imported and not reported as
  errors.
- Severity comes from the result's own ``level`` if present; otherwise from
  the rule's ``defaultConfiguration.level``; otherwise SARIF's own default
  of ``"warning"`` for a fail-kind result (§3.27.10, §3.49.8).
- The finding's location is the first location's ``physicalLocation``
  (§3.29): ``artifactLocation.uri`` plus ``region.startLine`` for evidence
  text only — deliberately excluded from the fingerprint, since fingerprint
  stability must not depend on a line number that shifts on every unrelated
  code change.
- ``partialFingerprints`` (§3.27.14) is treated as an opaque, producer-owned
  bag of values: preserved verbatim in ``raw_data`` and folded into the
  fingerprint as a whole (sorted key=value pairs), but no individual key
  (e.g. a specific hash algorithm name) is given special meaning — SARIF
  does not standardize what producers put in there.
- A rule's ``helpUri`` (§3.49.9), when present, is preserved as a reference.

Unsupported/uninterpreted SARIF constructs (suppressions, related
locations, code flows, fixes, run-level ``originalUriBaseIds``, nested
artifact URIs) are intentionally not extracted — they don't map to
Threnda's current finding model — and are preserved only insofar as the
whole result object is kept in ``raw_data``.
"""

from __future__ import annotations

import json
from typing import Any

from threnda.app.models.enums import AssetType, SeverityLevel
from threnda.app.services.ingestion.base import (
    ImportFormatError,
    NormalizedAsset,
    NormalizedFinding,
    ParsedImport,
    RowError,
)
from threnda.app.services.ingestion.fingerprint import compute_fingerprint

_LEVEL_TO_SEVERITY: dict[str, SeverityLevel] = {
    "error": SeverityLevel.HIGH,
    "warning": SeverityLevel.MEDIUM,
    "note": SeverityLevel.LOW,
    "none": SeverityLevel.INFORMATIONAL,
}

# SARIF result kinds that represent an actual issue. Everything else
# ("pass", "notApplicable", "review", "open", "informational") is a
# non-failure outcome, not a security finding.
_FAILING_KIND = "fail"


class _SkipResult(Exception):
    """Internal signal: this result is a non-failure kind, not an error."""


class SarifImporter:
    format_name = "sarif"

    def detect(self, filename: str, content_type: str | None, raw: bytes) -> bool:
        lowered_name = filename.lower()
        if lowered_name.endswith(".sarif") or lowered_name.endswith(".sarif.json"):
            return True
        if content_type and "sarif" in content_type.lower():
            return True
        head = raw[:65536]
        return b'"runs"' in head and b'"tool"' in head

    def parse(self, raw: bytes) -> ParsedImport:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ImportFormatError(f"Not valid UTF-8: {exc}") from exc

        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ImportFormatError(f"Not valid JSON: {exc}") from exc

        if not isinstance(document, dict) or not isinstance(document.get("runs"), list):
            raise ImportFormatError('Expected a SARIF document with a top-level "runs" array')
        if not document["runs"]:
            raise ImportFormatError("SARIF document has no runs")

        findings: list[NormalizedFinding] = []
        errors: list[RowError] = []
        total = 0

        for run_index, run in enumerate(document["runs"]):
            if not isinstance(run, dict):
                errors.append(
                    RowError(index=run_index, message=f"run {run_index} is not an object")
                )
                continue

            rules_by_id, rules_by_index = self._index_rules(run)
            results = run.get("results")
            if not isinstance(results, list):
                continue

            for result_index, result in enumerate(results):
                total += 1
                try:
                    findings.append(
                        self._normalize_result(
                            run_index, result_index, rules_by_id, rules_by_index, result
                        )
                    )
                except _SkipResult:
                    # Not a failure and not an error: neither `findings` nor
                    # `errors` grows, but it still counts toward
                    # `total_records` so the summary reflects everything the
                    # file actually contained.
                    pass
                except ValueError as exc:
                    errors.append(
                        RowError(
                            index=result_index,
                            message=f"run {run_index}, result {result_index}: {exc}",
                        )
                    )

        return ParsedImport(findings=tuple(findings), errors=tuple(errors), total_records=total)

    @staticmethod
    def _index_rules(
        run: dict[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], dict[int, dict[str, Any]]]:
        driver = ((run.get("tool") or {}).get("driver")) or {}
        rules = driver.get("rules")
        by_id: dict[str, dict[str, Any]] = {}
        by_index: dict[int, dict[str, Any]] = {}
        if isinstance(rules, list):
            for position, rule in enumerate(rules):
                if not isinstance(rule, dict):
                    continue
                by_index[position] = rule
                rule_id = rule.get("id")
                if isinstance(rule_id, str):
                    by_id[rule_id] = rule
        return by_id, by_index

    def _normalize_result(
        self,
        run_index: int,
        result_index: int,
        rules_by_id: dict[str, dict[str, Any]],
        rules_by_index: dict[int, dict[str, Any]],
        result: Any,
    ) -> NormalizedFinding:
        if not isinstance(result, dict):
            raise ValueError("result is not an object")

        if result.get("kind", _FAILING_KIND) != _FAILING_KIND:
            raise _SkipResult

        rule_id = result.get("ruleId")
        rule: dict[str, Any] | None = None
        if isinstance(rule_id, str) and rule_id in rules_by_id:
            rule = rules_by_id[rule_id]
        else:
            rule_index = result.get("ruleIndex")
            if isinstance(rule_index, int):
                rule = rules_by_index.get(rule_index)
                if rule is not None and not rule_id:
                    rule_id = rule.get("id")

        message = result.get("message")
        title = message.get("text") if isinstance(message, dict) else None
        if not title and rule is not None:
            short_description = rule.get("shortDescription")
            if isinstance(short_description, dict):
                title = short_description.get("text")
        if not title:
            title = rule_id or f"SARIF result {run_index}.{result_index}"

        level = result.get("level")
        if not isinstance(level, str) and rule is not None:
            default_configuration = rule.get("defaultConfiguration")
            if isinstance(default_configuration, dict):
                level = default_configuration.get("level")
        if not isinstance(level, str):
            level = "warning"
        severity = _LEVEL_TO_SEVERITY.get(level, SeverityLevel.MEDIUM)

        references: list[str] = []
        if rule is not None:
            help_uri = rule.get("helpUri")
            if isinstance(help_uri, str):
                references.append(help_uri)

        uri, start_line = self._first_physical_location(result)
        evidence = None
        if uri and start_line is not None:
            evidence = f"{uri}:{start_line}"
        elif uri:
            evidence = uri

        partial_fingerprints = result.get("partialFingerprints")
        fingerprint_material: str | None = None
        if isinstance(partial_fingerprints, dict) and partial_fingerprints:
            fingerprint_material = "|".join(
                f"{key}={value}" for key, value in sorted(partial_fingerprints.items())
            )

        if fingerprint_material:
            source_finding_id = fingerprint_material
        elif rule_id and uri:
            source_finding_id = f"{rule_id}@{uri}"
        else:
            source_finding_id = f"run{run_index}-result{result_index}"

        # Deliberately excludes `start_line`: fingerprint stability must not
        # depend on a line number that shifts on every unrelated edit.
        fingerprint = compute_fingerprint(rule_id, uri, fingerprint_material)

        asset = None
        if uri:
            asset = NormalizedAsset(
                canonical_key=f"repository_file:{uri}", name=uri, asset_type=AssetType.REPOSITORY
            )

        return NormalizedFinding(
            source_finding_id=source_finding_id,
            fingerprint=fingerprint,
            title=title,
            severity=severity,
            cwe=None,
            evidence=evidence,
            references=tuple(references),
            asset=asset,
            raw_data=result,
        )

    @staticmethod
    def _first_physical_location(result: dict[str, Any]) -> tuple[str | None, int | None]:
        locations = result.get("locations")
        if not isinstance(locations, list) or not locations:
            return None, None
        first = locations[0]
        if not isinstance(first, dict):
            return None, None
        physical = first.get("physicalLocation")
        if not isinstance(physical, dict):
            return None, None
        artifact_location = physical.get("artifactLocation")
        uri = artifact_location.get("uri") if isinstance(artifact_location, dict) else None
        region = physical.get("region")
        start_line = region.get("startLine") if isinstance(region, dict) else None
        return (
            uri if isinstance(uri, str) else None,
            start_line if isinstance(start_line, int) else None,
        )
