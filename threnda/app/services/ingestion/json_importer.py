"""Generic JSON importer for Threnda's native finding shape.

Expected shape (see demo-data/json/synthetic-findings.json and
docs/importers.md for the full field reference)::

    {
      "findings": [
        {
          "id": "unique-within-this-source",
          "title": "...",
          "description": "...",
          "severity": "high",          // or omit and give "cvss" instead
          "cvss": 7.5,
          "cve": "CVE-2024-12345",
          "cwe": "CWE-79",
          "asset": {"key": "host:example.test", "name": "example.test", "type": "host"},
          "evidence": "...",
          "remediation": "...",
          "exploit_evidence": false,
          "references": ["https://example.test/advisory"]
        }
      ]
    }

This is Threnda's own format for re-importing previously-exported or
hand-authored findings — not tied to any particular scanner.
"""

from __future__ import annotations

import json
from typing import Any

from threnda.app.models.enums import AssetType
from threnda.app.services.ingestion.base import (
    ImportFormatError,
    NormalizedAsset,
    NormalizedFinding,
    ParsedImport,
    RowError,
)
from threnda.app.services.ingestion.fingerprint import compute_fingerprint
from threnda.app.services.ingestion.severity import (
    normalize_severity_name,
    parse_cvss_score,
    severity_from_cvss,
)


class JsonImporter:
    format_name = "json"

    def detect(self, filename: str, content_type: str | None, raw: bytes) -> bool:
        if content_type and "json" in content_type.lower():
            return True
        if filename.lower().endswith(".json"):
            return True
        stripped = raw.lstrip()
        return stripped[:1] in (b"{", b"[")

    def parse(self, raw: bytes) -> ParsedImport:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ImportFormatError(f"Not valid UTF-8: {exc}") from exc

        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ImportFormatError(f"Not valid JSON: {exc}") from exc

        if not isinstance(document, dict) or "findings" not in document:
            raise ImportFormatError('Expected a JSON object with a top-level "findings" array')

        raw_findings = document["findings"]
        if not isinstance(raw_findings, list):
            raise ImportFormatError('"findings" must be an array')

        findings: list[NormalizedFinding] = []
        errors: list[RowError] = []

        for index, item in enumerate(raw_findings):
            try:
                findings.append(self._normalize_one(index, item))
            except ValueError as exc:
                errors.append(RowError(index=index, message=str(exc)))

        return ParsedImport(
            findings=tuple(findings), errors=tuple(errors), total_records=len(raw_findings)
        )

    def _normalize_one(self, index: int, item: Any) -> NormalizedFinding:
        if not isinstance(item, dict):
            raise ValueError("each finding must be a JSON object")

        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError('"title" is required')

        cvss_score: float | None = None
        if "cvss" in item and item["cvss"] is not None:
            cvss_score = parse_cvss_score(item["cvss"])

        severity = normalize_severity_name(item.get("severity"))
        if severity is None and cvss_score is not None:
            severity = severity_from_cvss(cvss_score)
        if severity is None:
            raise ValueError('"severity" is missing or unrecognized, and no "cvss" was given')

        source_finding_id = str(item.get("id") or f"row-{index}")
        cve = item.get("cve")
        references = item.get("references") or []
        if not isinstance(references, list) or not all(isinstance(r, str) for r in references):
            raise ValueError('"references" must be an array of strings')

        asset = self._normalize_asset(item.get("asset"))

        exploit_evidence = item.get("exploit_evidence")
        if exploit_evidence is not None and not isinstance(exploit_evidence, bool):
            raise ValueError('"exploit_evidence" must be true, false, or omitted')

        return NormalizedFinding(
            source_finding_id=source_finding_id,
            fingerprint=compute_fingerprint(cve, asset.canonical_key if asset else None, title),
            title=title.strip(),
            severity=severity,
            description=item.get("description"),
            cvss_score=cvss_score,
            cve=cve,
            cwe=item.get("cwe"),
            evidence=item.get("evidence"),
            remediation=item.get("remediation"),
            exploit_evidence=exploit_evidence,
            references=tuple(references),
            asset=asset,
            raw_data=item,
        )

    @staticmethod
    def _normalize_asset(raw_asset: Any) -> NormalizedAsset | None:
        if raw_asset is None:
            return None
        if not isinstance(raw_asset, dict):
            raise ValueError('"asset" must be an object')
        key = raw_asset.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError('"asset.key" is required when "asset" is given')
        name = raw_asset.get("name") or key
        asset_type = AssetType.UNKNOWN
        raw_type = raw_asset.get("type")
        if isinstance(raw_type, str):
            try:
                asset_type = AssetType(raw_type.strip().lower())
            except ValueError:
                asset_type = AssetType.UNKNOWN
        return NormalizedAsset(canonical_key=key.strip(), name=name, asset_type=asset_type)
