"""Generic CSV importer with an explicit column mapping.

A ``CsvImporter`` is bound to one mapping (``{csv_column_name: target_field}``)
at construction time — resolving *which* mapping to use (client-supplied,
previously saved for this source, or "none available, ask the user") is the
API layer's job (see ``threnda.app.api.v1.imports``), not this class's.

Supported target fields: ``title`` (required), ``asset``, ``severity``,
``cvss``, ``cve``, ``cwe``, ``description``, ``remediation``, ``evidence``.
Unmapped columns in the file are ignored, not an error — a mapping only
needs to cover the columns Threnda should read.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable

from threnda.app.models.enums import AssetType, SeverityLevel
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

KNOWN_TARGET_FIELDS = frozenset(
    {"title", "asset", "severity", "cvss", "cve", "cwe", "description", "remediation", "evidence"}
)

#: Best-effort mapping guess from common export column names, used by the
#: API layer to pre-fill the mapping wizard. Never used silently — the
#: caller must confirm (or override) it before it's saved for a source.
HEADER_ALIASES: dict[str, str] = {
    "vulnerability": "title",
    "finding": "title",
    "title": "title",
    "issue": "title",
    "host": "asset",
    "asset": "asset",
    "target": "asset",
    "hostname": "asset",
    "severity": "severity",
    "risk": "severity",
    "cvss": "cvss",
    "cvss score": "cvss",
    "cvss_score": "cvss",
    "base score": "cvss",
    "cve": "cve",
    "cve id": "cve",
    "cwe": "cwe",
    "description": "description",
    "summary": "description",
    "solution": "remediation",
    "remediation": "remediation",
    "fix": "remediation",
    "recommendation": "remediation",
    "evidence": "evidence",
    "proof": "evidence",
}


def guess_mapping(headers: list[str]) -> dict[str, str]:
    """Best-effort column -> target-field guess for the mapping wizard's initial state."""
    mapping: dict[str, str] = {}
    used_targets: set[str] = set()
    for header in headers:
        target = HEADER_ALIASES.get(header.strip().lower())
        if target and target not in used_targets:
            mapping[header] = target
            used_targets.add(target)
    return mapping


class CsvImporter:
    format_name = "csv"

    def __init__(
        self,
        mapping: dict[str, str],
        *,
        header_row_index: int = 0,
        asset_type: AssetType = AssetType.HOST,
        severity_normalizer: Callable[[str], SeverityLevel | None] | None = None,
        profile_slug: str | None = None,
        profile_name: str | None = None,
    ) -> None:
        unknown_targets = set(mapping.values()) - KNOWN_TARGET_FIELDS
        if unknown_targets:
            raise ValueError(f"Unknown mapping target field(s): {sorted(unknown_targets)}")
        targets = list(mapping.values())
        duplicate_targets = {target for target in targets if targets.count(target) > 1}
        if duplicate_targets:
            raise ValueError(
                f"Multiple columns mapped to the same field: {sorted(duplicate_targets)}"
            )
        if "title" not in mapping.values():
            raise ValueError('The mapping must map some column to "title"')
        if header_row_index < 0:
            raise ValueError("header_row_index cannot be negative")
        self.mapping = mapping
        self.header_row_index = header_row_index
        self.asset_type = asset_type
        self.severity_normalizer = severity_normalizer
        self.profile_slug = profile_slug
        self.profile_name = profile_name

    def detect(self, filename: str, content_type: str | None, raw: bytes) -> bool:
        if content_type and "csv" in content_type.lower():
            return True
        if filename.lower().endswith(".csv"):
            return True
        try:
            first_line = raw[:4096].decode("utf-8", errors="ignore").splitlines()[0]
        except IndexError:
            return False
        return "," in first_line

    def parse(self, raw: bytes) -> ParsedImport:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ImportFormatError(f"Not valid UTF-8: {exc}") from exc

        rows = list(csv.reader(io.StringIO(text)))
        header_row = rows[self.header_row_index] if len(rows) > self.header_row_index else None
        if not header_row:
            raise ImportFormatError("CSV file has no header row")

        seen: set[str] = set()
        duplicates: set[str] = set()
        for column in header_row:
            key = column.strip()
            if key in seen:
                duplicates.add(key)
            seen.add(key)
        if duplicates:
            raise ImportFormatError(f"Duplicate CSV column header(s): {sorted(duplicates)}")

        missing_columns = set(self.mapping) - set(header_row)
        if missing_columns:
            raise ImportFormatError(
                f"Mapped column(s) not present in this file: {sorted(missing_columns)}"
            )

        findings: list[NormalizedFinding] = []
        errors: list[RowError] = []
        total = 0
        for index, cells in enumerate(rows[self.header_row_index + 1 :]):
            total += 1
            row = {
                column: cells[position] if position < len(cells) else None
                for position, column in enumerate(header_row)
            }
            try:
                findings.append(self._normalize_row(row))
            except ValueError as exc:
                errors.append(RowError(index=index, message=str(exc)))

        return ParsedImport(findings=tuple(findings), errors=tuple(errors), total_records=total)

    def _normalize_row(self, row: dict[str, str | None]) -> NormalizedFinding:
        values: dict[str, str] = {
            target: (row.get(column) or "").strip() for column, target in self.mapping.items()
        }

        title = values.get("title", "")
        if not title:
            raise ValueError('"title" is empty for this row')

        cvss_score: float | None = None
        if values.get("cvss"):
            cvss_score = parse_cvss_score(values["cvss"])

        raw_severity = values.get("severity", "")
        severity = normalize_severity_name(raw_severity)
        if severity is None and raw_severity and self.severity_normalizer is not None:
            normalized = self.severity_normalizer(raw_severity)
            if normalized is not None:
                severity = normalized
        if severity is None and cvss_score is not None:
            severity = severity_from_cvss(cvss_score)
        if severity is None:
            raise ValueError("severity is missing/unrecognized and no valid cvss was given")

        asset: NormalizedAsset | None = None
        asset_value = values.get("asset")
        if asset_value:
            asset = NormalizedAsset(
                canonical_key=f"{self.asset_type.value}:{asset_value.lower()}",
                name=asset_value,
                asset_type=self.asset_type,
            )

        cve = values.get("cve") or None
        # CSV rows carry no producer-supplied identity (unlike JSON's "id"
        # or SARIF's ruleId/partialFingerprints) — a positional `row-{index}`
        # identity would treat a re-export with the same rows in a different
        # order as N resolved + N new findings, which is wrong far more often
        # than the alternative below is. The documented fallback is a
        # content signature over the same identifying fields as
        # `fingerprint`: stable across reordering and across re-imports of
        # genuinely unchanged data, at the honest cost that editing a row's
        # title/cve/asset in the CSV is indistinguishable from "resolved,
        # then a new one appeared" — there is no external id to prove
        # otherwise for this format. See docs/deduplication.md.
        identity = compute_fingerprint(cve, asset.canonical_key if asset else None, title)
        return NormalizedFinding(
            source_finding_id=identity,
            fingerprint=identity,
            title=title,
            severity=severity,
            description=values.get("description") or None,
            cvss_score=cvss_score,
            cve=cve,
            cwe=values.get("cwe") or None,
            evidence=values.get("evidence") or None,
            remediation=values.get("remediation") or None,
            asset=asset,
            raw_data={k: v for k, v in row.items() if k is not None},
        )
