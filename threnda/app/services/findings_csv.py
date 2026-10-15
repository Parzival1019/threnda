"""The pentest findings CSV template: strict validation, then an all-or-nothing import.

This is the import a consultancy uses for its *own* pentest results — one
row per finding, in a fixed, documented column layout that ships as a
downloadable template (``TEMPLATE_CSV``). It is deliberately stricter than
the generic scanner-CSV mapping wizard (``csv_importer.py``):

- the whole file is validated first, and **nothing is written** if any row
  has a problem — the user gets every problem at once, each with its line
  number and column, instead of a half-imported report;
- unknown column names are an error (a typo such as ``ownr`` must not be
  silently ignored).

Identity rules (what makes a re-import *not* create duplicates) — also in
docs/findings-csv.md:

1. If the ``id`` column has a value, that value is the finding's identity
   within the project ("PT-001"). Re-importing a row with the same id
   updates that finding, even if its title or severity changed.
2. Without an ``id``, the identity is a signature of ``title`` +
   ``affected_system`` + ``cve`` (case/whitespace-insensitive). Re-importing
   an identical row is recognised; editing one of those three fields in the
   file looks like a *different* finding. Use ``id`` to avoid that.

Re-import behaviour:

- report content (title, description, severity, affected system,
  remediation) follows the file, and every change is recorded in the
  finding's history;
- workflow fields (owner, due date) are only filled in when still empty in
  Threnda — edits made in Threnda are never overwritten by a re-import;
- status is never set by an import. A finding missing from a later import
  is only *marked* "not in latest import" at the source level; it stays in
  its current status and is never closed — closing requires a retest.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.enums import ExposureHistoryEventType, SeverityLevel, SourceType
from threnda.app.models.exposure import Exposure
from threnda.app.models.exposure_history import ExposureHistory
from threnda.app.models.finding import Finding
from threnda.app.models.import_run import ImportRun
from threnda.app.models.workspace import Workspace
from threnda.app.services.exposures import ExposureChanges, apply_exposure_update
from threnda.app.services.ingestion.base import NormalizedFinding, ParsedImport
from threnda.app.services.ingestion.fingerprint import compute_fingerprint
from threnda.app.services.ingestion.runner import persist_parsed_import
from threnda.app.services.ingestion.severity import parse_cvss_score
from threnda.app.services.manual_findings import asset_for_system, get_or_create_source

TEMPLATE_SOURCE_NAME = "Pentest findings (CSV template)"

#: Column order of the downloadable template. ``title`` and ``severity`` are
#: required; every other column is optional and may be left out entirely.
TEMPLATE_COLUMNS = (
    "id",
    "title",
    "severity",
    "affected_system",
    "description",
    "remediation",
    "owner",
    "due_date",
    "cvss",
    "cve",
    "cwe",
    "evidence",
)
REQUIRED_COLUMNS = ("title", "severity")

_SEVERITY_ALIASES = {
    "critical": SeverityLevel.CRITICAL,
    "high": SeverityLevel.HIGH,
    "medium": SeverityLevel.MEDIUM,
    "moderate": SeverityLevel.MEDIUM,
    "low": SeverityLevel.LOW,
    "info": SeverityLevel.INFORMATIONAL,
    "informational": SeverityLevel.INFORMATIONAL,
}

_MAX_LENGTHS = {
    "id": 100,
    "title": 500,
    "affected_system": 500,
    "owner": 200,
    "cve": 50,
    "cwe": 50,
}

#: Synthetic example rows (RFC 2606 ``.example`` hosts only).
TEMPLATE_CSV = (
    ",".join(TEMPLATE_COLUMNS)
    + "\r\n"
    + "PT-001,SQL injection in login form,critical,portal.client.example,"
    '"The username parameter is concatenated into a SQL query.",'
    '"Use parameterised queries.",Application team,2026-12-01,9.8,,CWE-89,'
    '"Payload \' OR 1=1-- returned all rows."\r\n'
    + "PT-002,Missing HSTS header,low,portal.client.example,"
    '"Responses do not set Strict-Transport-Security.",'
    '"Send Strict-Transport-Security with a long max-age.",,,,,CWE-319,\r\n'
)


@dataclass(frozen=True)
class CsvProblem:
    """One validation problem. ``line`` is the 1-based line in the file
    (the header is line 1), ``column`` the offending column if any."""

    line: int
    column: str | None
    message: str


@dataclass(frozen=True)
class TemplateRow:
    line: int
    identity: str
    id: str | None
    title: str
    severity: SeverityLevel
    affected_system: str | None
    description: str | None
    remediation: str | None
    owner: str | None
    due_date: date | None
    cvss: float | None
    cve: str | None
    cwe: str | None
    evidence: str | None


@dataclass
class TemplateImportResult:
    problems: list[CsvProblem] = field(default_factory=list)
    total_rows: int = 0
    import_run: ImportRun | None = None
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    reopened: int = 0
    not_in_this_import: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems and self.import_run is not None


def _row_identity(id_value: str | None, title: str, system: str | None, cve: str | None) -> str:
    if id_value:
        return f"id:{id_value.strip().lower()}"
    asset_key = asset_for_system(system).canonical_key if system else None
    return compute_fingerprint(cve, asset_key, title)


def parse_template_csv(raw: bytes) -> tuple[list[TemplateRow], list[CsvProblem]]:
    problems: list[CsvProblem] = []
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [
            CsvProblem(1, None, "The file is not UTF-8 encoded text. Save it as CSV UTF-8.")
        ]

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return [], [CsvProblem(1, None, "The file is empty.")]
    except csv.Error as exc:
        return [], [CsvProblem(1, None, f"The header row is not valid CSV: {exc}")]

    columns = [h.strip().lower() for h in header]
    seen: set[str] = set()
    header_unusable = False
    for name in columns:
        if not name:
            header_unusable = True
            problems.append(CsvProblem(1, None, "A column has an empty header name."))
        elif name not in TEMPLATE_COLUMNS:
            problems.append(
                CsvProblem(
                    1,
                    name,
                    f"Unknown column {name!r}. Allowed columns: {', '.join(TEMPLATE_COLUMNS)}.",
                )
            )
        elif name in seen:
            header_unusable = True
            problems.append(CsvProblem(1, name, f"Column {name!r} appears more than once."))
        seen.add(name)
    for required in REQUIRED_COLUMNS:
        if required not in columns:
            header_unusable = True
            problems.append(CsvProblem(1, required, f"Required column {required!r} is missing."))
    # Unknown columns alone don't stop row validation (their values are
    # simply not read), so one upload reports every problem in the file.
    # A missing required column or a duplicated header does: rows can't be
    # interpreted reliably then.
    if header_unusable:
        return [], problems

    rows: list[TemplateRow] = []
    identities: dict[str, int] = {}
    line = 1
    try:
        for cells in reader:
            line = reader.line_num
            if not any(cell.strip() for cell in cells):
                continue  # blank line
            if len(cells) > len(columns):
                problems.append(
                    CsvProblem(
                        line,
                        None,
                        f"This row has {len(cells)} values but the header has {len(columns)} "
                        "columns (check for an unquoted comma).",
                    )
                )
                continue
            values = {
                name: (cells[i].strip() if i < len(cells) else "") for i, name in enumerate(columns)
            }
            row = _validate_row(line, values, problems)
            if row is None:
                continue
            if row.identity in identities:
                what = f"id {row.id!r}" if row.id else "title + affected system + CVE"
                problems.append(
                    CsvProblem(
                        line,
                        "id" if row.id else "title",
                        f"Duplicate of line {identities[row.identity]} (same {what}). "
                        "Each finding must appear once.",
                    )
                )
                continue
            identities[row.identity] = line
            rows.append(row)
    except csv.Error as exc:
        problems.append(CsvProblem(line + 1, None, f"Not valid CSV: {exc}"))

    if not rows and not problems:
        problems.append(CsvProblem(2, None, "The file has a header but no finding rows."))
    return rows, problems


def _validate_row(
    line: int, values: dict[str, str], problems: list[CsvProblem]
) -> TemplateRow | None:
    before = len(problems)

    def problem(column: str, message: str) -> None:
        problems.append(CsvProblem(line, column, message))

    for column, limit in _MAX_LENGTHS.items():
        if len(values.get(column, "")) > limit:
            problem(column, f"Too long ({len(values[column])} characters; maximum {limit}).")

    title = values.get("title", "")
    if not title:
        problem("title", "Title is required.")

    raw_severity = values.get("severity", "")
    severity = _SEVERITY_ALIASES.get(raw_severity.lower())
    if severity is None:
        problem(
            "severity",
            f"Severity {raw_severity!r} is not recognised. Use one of: critical, high, "
            "medium, low, info."
            if raw_severity
            else "Severity is required.",
        )

    due: date | None = None
    if values.get("due_date"):
        try:
            due = date.fromisoformat(values["due_date"])
        except ValueError:
            problem(
                "due_date",
                f"Due date {values['due_date']!r} is not a valid date. Use YYYY-MM-DD, "
                "e.g. 2026-12-31.",
            )

    cvss: float | None = None
    if values.get("cvss"):
        try:
            cvss = parse_cvss_score(values["cvss"])
        except ValueError:
            problem("cvss", f"CVSS {values['cvss']!r} must be a number between 0.0 and 10.0.")

    if len(problems) > before or severity is None:
        return None

    def opt(column: str) -> str | None:
        return values.get(column) or None

    system = opt("affected_system")
    cve = opt("cve")
    return TemplateRow(
        line=line,
        identity=_row_identity(opt("id"), title, system, cve),
        id=opt("id"),
        title=title,
        severity=severity,
        affected_system=system,
        description=opt("description"),
        remediation=opt("remediation"),
        owner=opt("owner"),
        due_date=due,
        cvss=cvss,
        cve=cve,
        cwe=opt("cwe"),
        evidence=opt("evidence"),
    )


def _normalized(row: TemplateRow) -> NormalizedFinding:
    asset = asset_for_system(row.affected_system) if row.affected_system else None
    return NormalizedFinding(
        source_finding_id=row.identity,
        fingerprint=compute_fingerprint(row.cve, asset.canonical_key if asset else None, row.title),
        title=row.title,
        severity=row.severity,
        description=row.description,
        cvss_score=row.cvss,
        cve=row.cve,
        cwe=row.cwe,
        evidence=row.evidence,
        remediation=row.remediation,
        asset=asset,
        raw_data={"line": row.line, "id": row.id},
    )


def import_template_csv(
    db: Session, workspace: Workspace, *, raw: bytes, filename: str, actor: str
) -> TemplateImportResult:
    """Validate, then (only if the whole file is valid) import it."""
    rows, problems = parse_template_csv(raw)
    result = TemplateImportResult(problems=problems, total_rows=len(rows))
    if problems:
        return result

    source = get_or_create_source(
        db, workspace, name=TEMPLATE_SOURCE_NAME, source_type=SourceType.CSV
    )
    existing_identities = set(
        db.execute(
            select(Finding.source_finding_id).where(
                Finding.workspace_id == workspace.id, Finding.source_id == source.id
            )
        ).scalars()
    )
    run = persist_parsed_import(
        db,
        workspace_id=workspace.id,
        source_id=source.id,
        filename=filename,
        raw=raw,
        parsed=ParsedImport(
            findings=tuple(_normalized(r) for r in rows), errors=(), total_records=len(rows)
        ),
        correlate=False,
    )
    findings_by_identity = {
        f.source_finding_id: f
        for f in db.execute(
            select(Finding).where(
                Finding.workspace_id == workspace.id, Finding.source_id == source.id
            )
        ).scalars()
    }
    actor_label = f"{actor} (import)"
    for row in rows:
        finding = findings_by_identity[row.identity]
        if finding.exposure_id is None:  # pragma: no cover - correlate=False always links
            continue
        exposure = db.get(Exposure, finding.exposure_id)
        if exposure is None:  # pragma: no cover - FK guarantees it exists
            continue
        changes: ExposureChanges = {}
        if row.identity not in existing_identities:
            db.add(
                ExposureHistory(
                    exposure=exposure,
                    event_type=ExposureHistoryEventType.CREATED,
                    new_value=f"imported from {filename} (line {row.line})",
                    actor=actor,
                )
            )
        else:
            # Report content follows the file (audited as DETAILS_CHANGED).
            changes.update(
                {
                    "title": row.title,
                    "description": row.description,
                    "severity": row.severity,
                    "affected_system": row.affected_system,
                }
            )
        if row.remediation and row.remediation != exposure.remediation:
            changes["remediation"] = row.remediation
        # Workflow fields: fill when empty, never overwrite Threnda-side edits.
        if row.owner and not exposure.owner:
            changes["owner"] = row.owner
        if row.due_date and exposure.remediation_target_date is None:
            changes["remediation_target_date"] = row.due_date
        if changes:
            apply_exposure_update(db, exposure, changes, note=None, actor=actor_label)

    result.import_run = run
    result.new = run.new_findings
    result.updated = run.updated_findings
    result.unchanged = run.unchanged_findings
    result.reopened = run.reopened_findings
    result.not_in_this_import = run.resolved_findings
    return result
