"""Strict, documented scanner CSV profiles.

Profiles only select a mapping when a row contains a distinctive set of
vendor columns.  This deliberately favours asking the user to map an unknown
file over silently importing it with the wrong semantics.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from threnda.app.models.enums import AssetType, SeverityLevel

SeverityScale = Literal["names", "qualys-1-5", "rapid7-1-10"]


def _key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


@dataclass(frozen=True)
class CsvScannerProfile:
    slug: str
    name: str
    documentation_url: str
    required_headers: frozenset[str]
    fields: Mapping[str, tuple[str, ...]]
    asset_type: AssetType = AssetType.HOST
    severity_scale: SeverityScale = "names"
    field_copies: tuple[tuple[str, str], ...] = ()

    def matches(self, headers: list[str]) -> bool:
        normalized = {_key(header) for header in headers}
        return {_key(header) for header in self.required_headers} <= normalized

    def mapping_for(self, headers: list[str]) -> dict[str, str]:
        actual = {_key(header): header for header in headers}
        mapping: dict[str, str] = {}
        for target, candidates in self.fields.items():
            for candidate in candidates:
                column = actual.get(_key(candidate))
                if column is not None:
                    mapping[column] = target
                    break
        return mapping

    def normalize_severity(self, raw: str) -> SeverityLevel | None:
        value = raw.strip()
        if not value:
            return None
        if self.severity_scale == "qualys-1-5":
            return {
                "1": SeverityLevel.LOW,
                "2": SeverityLevel.MEDIUM,
                "3": SeverityLevel.HIGH,
                "4": SeverityLevel.CRITICAL,
                "5": SeverityLevel.CRITICAL,
            }.get(value)
        if self.severity_scale == "rapid7-1-10":
            try:
                score = int(value)
            except ValueError:
                return None
            if 1 <= score <= 3:
                return SeverityLevel.MEDIUM
            if 4 <= score <= 7:
                return SeverityLevel.HIGH
            if 8 <= score <= 10:
                return SeverityLevel.CRITICAL
        return None


def _fields(**values: tuple[str, ...]) -> Mapping[str, tuple[str, ...]]:
    return MappingProxyType(values)


# Column names come from each vendor's public reporting documentation.  Some
# products allow users to customize exports, so each target may list a small
# set of documented version variants while the signature remains strict.
SCANNER_CSV_PROFILES: tuple[CsvScannerProfile, ...] = (
    CsvScannerProfile(
        slug="tenable-vm",
        name="Tenable Vulnerability Management",
        documentation_url=(
            "https://docs.tenable.com/vulnerability-management/Content/Explore/"
            "export-findings-csv-keys.htm"
        ),
        required_headers=frozenset({"Finding ID", "Asset ID", "Plugin ID", "Plugin Name"}),
        fields=_fields(
            title=("Plugin Name",),
            asset=("Host", "Asset Name"),
            severity=("Severity", "Original Severity"),
            cvss=("CVSSv4 Base Score", "CVSSv3 Base Score", "CVSSv2 Base Score"),
            cve=("CVE",),
            cwe=("CWE",),
            description=("Plugin Description", "Synopsis"),
            remediation=("Solution",),
            evidence=("Plugin Output",),
        ),
    ),
    CsvScannerProfile(
        slug="nessus",
        name="Nessus (classic CSV)",
        documentation_url="https://docs.tenable.com/nessus/Content/Export.htm",
        required_headers=frozenset({"Plugin ID", "Risk", "Host", "Name", "Plugin Output"}),
        fields=_fields(
            title=("Name",),
            asset=("Host",),
            severity=("Risk",),
            cvss=("CVSS v3.0 Base Score", "CVSS v2.0 Base Score", "CVSS"),
            cve=("CVE",),
            description=("Description", "Synopsis"),
            remediation=("Solution",),
            evidence=("Plugin Output",),
        ),
    ),
    CsvScannerProfile(
        slug="qualys-vmdr",
        name="Qualys VMDR",
        documentation_url=(
            "https://docs.qualys.com/en/vm/qweb-all-api/mergedProjects/qapi-rep/reports/"
            "download_saved_report.htm"
        ),
        required_headers=frozenset({"IP", "QID", "Title", "Vuln Status", "Type"}),
        fields=_fields(
            title=("Title",),
            asset=("IP", "DNS"),
            severity=("Severity",),
            cvss=("CVSS3 Base", "CVSS Base"),
            cve=("CVE ID", "CVE"),
            description=("Threat",),
            remediation=("Solution",),
            evidence=("Results",),
        ),
        asset_type=AssetType.IP,
        severity_scale="qualys-1-5",
    ),
    CsvScannerProfile(
        slug="rapid7-insightvm",
        name="Rapid7 InsightVM",
        documentation_url="https://docs.rapid7.com/insightvm/report-templates-and-sections/",
        required_headers=frozenset(
            {
                "Asset IP Address",
                "Vulnerability ID",
                "Vulnerability Title",
                "Vulnerability Severity Level",
            }
        ),
        fields=_fields(
            title=("Vulnerability Title",),
            asset=("Asset IP Address", "Asset Names"),
            severity=("Vulnerability Severity Level",),
            cvss=("Vulnerability CVSSv3 Score", "Vulnerability CVSS Score"),
            cve=("Vulnerability CVE IDs",),
            description=("Vulnerability Description",),
            remediation=("Vulnerability Solution",),
            evidence=("Vulnerability Proof", "Vulnerability Test Result Description"),
        ),
        asset_type=AssetType.IP,
        severity_scale="rapid7-1-10",
    ),
    CsvScannerProfile(
        slug="burp-suite-dast",
        name="Burp Suite DAST",
        documentation_url=(
            "https://portswigger.net/burp/documentation/dast/user-guide/work-with-scan-results/"
            "managing-issues"
        ),
        required_headers=frozenset({"Issue type", "Origin", "Path", "Severity", "Confidence"}),
        fields=_fields(
            title=("Issue type",),
            asset=("Origin", "Site name"),
            severity=("Severity",),
            evidence=("Path",),
        ),
        asset_type=AssetType.URL,
    ),
    CsvScannerProfile(
        slug="acunetix",
        name="Acunetix",
        documentation_url=(
            "https://www.acunetix.com/support/docs/managing-and-prioritizing-vulnerabilities/"
        ),
        required_headers=frozenset(
            {"Vulnerability", "URL", "Parameter", "Confidence %", "Vulnerability Id"}
        ),
        fields=_fields(
            title=("Vulnerability",),
            asset=("URL",),
            severity=("Severity",),
            evidence=("Parameter",),
        ),
        asset_type=AssetType.URL,
    ),
    CsvScannerProfile(
        slug="greenbone-openvas",
        name="Greenbone / OpenVAS",
        documentation_url="https://greenbone.github.io/python-gvm/api/api.html",
        required_headers=frozenset({"IP", "NVT Name", "NVT OID", "Specific Result", "CVEs"}),
        fields=_fields(
            title=("NVT Name",),
            asset=("IP", "Hostname"),
            cvss=("Severity", "CVSS"),
            cve=("CVEs",),
            description=("Summary", "Impact"),
            remediation=("Solution",),
            evidence=("Specific Result",),
        ),
        asset_type=AssetType.IP,
    ),
    CsvScannerProfile(
        slug="red-hat-acs",
        name="Red Hat Advanced Cluster Security (RHACS)",
        documentation_url=(
            "https://docs.redhat.com/en/documentation/"
            "red_hat_advanced_cluster_security_for_kubernetes/4.11/html/operating/"
            "managing-vulnerabilities"
        ),
        required_headers=frozenset(
            {"Cluster", "Namespace", "Deployment", "Image", "Component", "CVE", "Fixable"}
        ),
        fields=_fields(
            asset=("Image",),
            severity=("Severity",),
            cvss=("CVSS",),
            cve=("CVE",),
            remediation=("CVE Fixed In",),
            evidence=("Component",),
        ),
        asset_type=AssetType.CONTAINER_IMAGE,
        field_copies=(("title", "cve"),),
    ),
    CsvScannerProfile(
        slug="gitlab-vulnerability-report",
        name="GitLab Vulnerability Report",
        documentation_url="https://docs.gitlab.com/user/application_security/vulnerability_report/",
        required_headers=frozenset(
            {"Group name", "Project name", "Report type", "Scanner name", "Vulnerability ID"}
        ),
        fields=_fields(
            title=("Vulnerability",),
            asset=("Full Path", "Project name"),
            severity=("Severity",),
            cve=("CVE",),
            cwe=("CWE",),
            description=("Basic details", "Additional information"),
            evidence=("Location",),
        ),
        asset_type=AssetType.REPOSITORY,
    ),
    CsvScannerProfile(
        slug="checkmarx-one",
        name="Checkmarx One",
        documentation_url=(
            "https://docs.checkmarx.com/en/34965-281619-global-csv-result-reports.html"
        ),
        required_headers=frozenset(
            {"Vulnerability Name", "Severity", "Project Name", "Scan ID", "Scanner"}
        ),
        fields=_fields(
            title=("Vulnerability Name",),
            asset=("Project Name",),
            severity=("Severity",),
            evidence=("Results Link",),
        ),
        asset_type=AssetType.REPOSITORY,
    ),
    CsvScannerProfile(
        slug="snyk-issues",
        name="Snyk Issues Detail",
        documentation_url=(
            "https://docs.snyk.io/manage-risk/analytics/reports-tab/issue-columns-dictionary"
        ),
        required_headers=frozenset(
            {"PROBLEM ID", "PROBLEM TITLE", "PRODUCT NAME", "ISSUE SEVERITY"}
        ),
        fields=_fields(
            title=("PROBLEM TITLE",),
            asset=("PROJECT NAME", "PROJECT PUBLIC ID"),
            severity=("ISSUE SEVERITY",),
            cvss=("SNYK CVSS SCORE",),
            cve=("CVE",),
            cwe=("CWE",),
            evidence=("PACKAGE NAME AND VERSION", "FILE PATH"),
            remediation=("FIXED IN VERSION",),
        ),
        asset_type=AssetType.REPOSITORY,
    ),
)


@dataclass(frozen=True)
class CsvProfileMatch:
    profile: CsvScannerProfile
    headers: list[str]
    mapping: dict[str, str]
    header_row_index: int


def csv_rows(raw: bytes, *, limit: int = 25) -> list[list[str]]:
    """Decode a small prefix into rows for header discovery."""
    text = raw.decode("utf-8-sig")
    rows: list[list[str]] = []
    for row in csv.reader(io.StringIO(text)):
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def detect_csv_profile(raw: bytes) -> CsvProfileMatch | None:
    """Return one unambiguous scanner profile match, otherwise ``None``."""
    try:
        rows = csv_rows(raw)
    except (UnicodeDecodeError, csv.Error):
        return None

    matches: list[CsvProfileMatch] = []
    for row_index, headers in enumerate(rows):
        if not headers:
            continue
        for profile in SCANNER_CSV_PROFILES:
            if profile.matches(headers):
                mapping = profile.mapping_for(headers)
                copied_targets = {target for target, _source in profile.field_copies}
                if "title" in mapping.values() or "title" in copied_targets:
                    matches.append(CsvProfileMatch(profile, headers, mapping, row_index))

    if not matches:
        return None
    matches.sort(key=lambda match: len(match.profile.required_headers), reverse=True)
    best_size = len(matches[0].profile.required_headers)
    best = [match for match in matches if len(match.profile.required_headers) == best_size]
    return best[0] if len(best) == 1 else None


def find_csv_header(raw: bytes) -> tuple[list[str], int] | None:
    """Find an auto-profile header, or fall back to the first CSV row."""
    match = detect_csv_profile(raw)
    if match is not None:
        return match.headers, match.header_row_index
    try:
        rows = csv_rows(raw, limit=1)
    except (UnicodeDecodeError, csv.Error):
        return None
    return (rows[0], 0) if rows else None
