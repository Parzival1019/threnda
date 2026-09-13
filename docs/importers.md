# Importers

Status: Milestones 2 and 6. Supported formats: **CSV**, **generic JSON**,
**SARIF 2.1.0**, and **structured pentest report JSON**. Everything here is
synthetic-data tested; see `demo-data/` for working examples of every format
this document describes. Unstructured PDF/text pentest reports are a
separate, local-only extraction path — see `docs/pentest-and-ai.md`, not
this document.

## Architecture

`threnda/app/services/ingestion/base.py` defines the shared interface:

- `Importer.detect(filename, content_type, raw) -> bool` — a cheap sniff
  used to reject content that doesn't match a source's declared format
  (see "Content validation" below). Never raises.
- `Importer.parse(raw) -> ParsedImport` — does the real work. Raises
  `ImportFormatError` if the input isn't this format *at all* (invalid
  JSON syntax, a CSV with no header row, a SARIF document with no `runs`).
  A problem with one individual record does **not** raise — it's recorded
  in `ParsedImport.errors` and parsing continues.

`threnda/app/services/ingestion/runner.py` persists a `ParsedImport` as an
`ImportRun` plus `Finding`/`Asset` rows.

## Provisioning a workspace and source

Before uploading anything, create a workspace and a source through the API
— no SQL shell step required:

```
POST /api/v1/workspaces               {"name": "..."}
POST /api/v1/sources?workspace_id=<id> {"name": "...", "source_type": "csv"}
```

`source_type` is one of `csv`, `json`, `sarif` or `pentest_report` for
anything Community can currently import (see `threnda/app/models/enums.py`
for the full vendor-neutral list — the others exist for future milestones
and are rejected by the upload endpoint today, with a `422` naming what is
supported).

## Uploading a file

`POST /api/v1/imports?workspace_id=<id>` — `multipart/form-data`:

| field         | required | meaning                                                             |
|---------------|----------|----------------------------------------------------------------------|
| `source_id`   | yes      | which `Source` (in `workspace_id`) this upload belongs to           |
| `file`        | yes      | the CSV/JSON/SARIF file                                              |
| `csv_mapping` | CSV only | JSON object `{"column name": "field"}` — see "CSV mapping" below     |

The format is determined by the **source's** `source_type` (`csv`, `json`
or `sarif`), not sniffed from the upload — a source is a stable declaration
of what kind of data it produces. `detect()` is still run against the
actual bytes and rejects a mismatch (e.g. uploading a SARIF file to a
source configured as CSV): `422` with an actionable message. `Source`
records for a supported type are provisioned like any other `Source` — no
undocumented SQL shell step is required.

Any request with a `source_id` that does not belong to `workspace_id`
returns `404` before anything is read from the upload — no `Finding` or
`Asset` row is written, and no other workspace's data is touched.

### Transaction semantics

- **The file doesn't parse as its declared format at all** (`ImportFormatError`):
  a `FAILED` `ImportRun` is still durably recorded (this is not rolled
  back — it's a real audit entry, not a discarded attempt), and the
  response is `422` with `{"detail": ..., "import_run": {...}}`. No
  `Finding`/`Asset` rows are written.
- **The file parses, but some records are individually invalid** (missing
  required field, unparseable CVSS, unrecognized severity with no CVSS to
  fall back on): those records are skipped and counted in
  `import_run.failed_records`; every other record is normally persisted.
  The `ImportRun` is `COMPLETED` regardless of whether `failed_records` is
  zero. The response is `201` with
  `{"import_run": {...}, "row_errors": [{"index": ..., "message": ...}, ...]}`
  — `row_errors` is always present (possibly empty), so a caller can never
  mistake "some records were silently discarded" for full success: check
  `failed_records`/`row_errors`, not just the HTTP status code.
- Nothing is ever partially written for a single record — either it fully
  persists (`Finding`, and its `Asset` if it names one) or it doesn't.

### Content validation and untrusted input

- Request bodies are capped (`THRENDA_MAX_UPLOAD_BYTES`, default 20 MiB),
  enforced at the ASGI layer (`threnda.app.core.middleware`) by counting
  bytes as they stream in — not by trusting `Content-Length`, which a
  client can omit (chunked transfer) or lie about. Oversized requests are
  rejected (`413`) before the body is fully buffered.
- The uploaded filename is **never** used to construct a filesystem path —
  Community processes uploads fully in memory and writes nothing to disk.
  It's still reduced to a bare basename (`../../etc/passwd` becomes
  `passwd`) before being stored as `ImportRun.filename`, purely for safe
  display.
- CVSS scores must be finite numbers in `[0.0, 10.0]`
  (`threnda/app/services/ingestion/severity.py`) — never `NaN`/`Infinity`,
  never silently clamped into range. An out-of-range or non-numeric value
  is a row error, not a corrupted-looking finding.
- Nothing here ever fetches a URL, path, schema or command named inside an
  uploaded file (a SARIF `helpUri`, a CSV "Solution" column, etc.) — such
  values are preserved as inert text/reference data only.

## CSV

Requires an explicit column mapping — Threnda does not guess which column
is the title. Supported target fields: `title` (required), `asset`,
`severity`, `cvss`, `cve`, `cwe`, `description`, `remediation`, `evidence`.
Columns not mentioned in the mapping are ignored, not an error.

The mapping is provided once (`csv_mapping` on the upload) and then
**saved on the `Source`** (`Source.meta["csv_mapping"]`) for reuse on
later uploads to that same source — scoped to that source/workspace by
construction, since `Source` itself already is. Sending `csv_mapping`
again on a later upload replaces the saved mapping (an explicit
correction, not silently merged). Uploading to a CSV source with no saved
mapping and no `csv_mapping` given is a `422` asking for one.

Rejected outright (whole-file `ImportFormatError`, not a per-row issue):

- no header row
- duplicate column headers
- a mapped column name that isn't actually present in the file's header
- a mapping whose target field isn't one of the known fields, or that maps
  two different columns to the same field

Per-row: an empty mapped `title` cell, or a `severity`/`cvss` that's both
missing/unrecognized (severity is derived from CVSS when the label is
missing or unrecognized, using the standard CVSS v3 bands).

Common severity spellings are recognized case-insensitively:
`informational`/`info`/`none`, `low`/`minor`, `medium`/`moderate`/`warning`,
`high`/`error`, `critical`/`severe`.

Example: `demo-data/csv/synthetic-vulnerabilities.csv`, mapping
`{"Vulnerability": "title", "Host": "asset", "Severity": "severity", "CVSS": "cvss", "CVE": "cve", "Description": "description", "Solution": "remediation"}`.
`demo-data/csv/synthetic-vulnerabilities-followup.csv` is a second export of
the same (synthetic) source with one finding resolved, one changed, and one
new — for exercising the Milestone 3 differential engine once it exists;
Milestone 2 doesn't yet compute new/resolved/changed itself.

## Generic JSON

Threnda's own format for re-importing previously exported or hand-authored
findings — not tied to a scanner:

```json
{
  "findings": [
    {
      "id": "unique-within-this-source",
      "title": "...",
      "description": "...",
      "severity": "high",
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
```

`title` is required. Either `severity` or `cvss` is required (a numeric
`cvss` without a recognized `severity` derives one). `asset.type` must be
one of the known asset types or it falls back to `unknown`. `exploit_evidence`
is optional (`true`/`false`, or omit if not known) — a Milestone 4
risk-scoring input (`docs/risk-scoring.md`); CSV and SARIF have no
equivalent public field, so only this importer sets it. Example:
`demo-data/json/synthetic-findings.json`.

## SARIF 2.1.0

Interpreted against the public [OASIS SARIF 2.1.0
specification](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html),
independent of any particular scanner:

- Every `run.results[]` entry is a candidate finding. `ruleId` is resolved
  first; if absent, `ruleIndex` is used to look the rule up in
  `tool.driver.rules[]`.
- **Only `kind: "fail"` results are imported** (`kind` defaults to `"fail"`
  when omitted). `pass`, `notApplicable`, `review`, `open` and
  `informational` results are recognized and intentionally skipped — not
  imported, and not reported as row errors, since they aren't failures.
  They still count toward `import_run.total_records` (so the summary
  reflects everything the file actually contained), just not toward
  `successful_records` or `failed_records` — the gap between
  `total_records` and `successful_records + failed_records` is exactly the
  count of non-`fail` results.
- Severity: the result's own `level` if present; otherwise the rule's
  `defaultConfiguration.level`; otherwise SARIF's own default of
  `"warning"`. Mapped to Threnda severity as `error → high`,
  `warning → medium`, `note → low`, `none → informational`.
- Location: the first result location's `physicalLocation` —
  `artifactLocation.uri` plus `region.startLine` for the evidence text.
  **The fingerprint deliberately excludes the line number** — it's derived
  from `ruleId` + artifact `uri` + `partialFingerprints` only, so it stays
  stable across unrelated edits that merely shift line numbers.
- `partialFingerprints` is preserved as an opaque, producer-owned bag of
  values (kept verbatim in `raw_data`, folded into the fingerprint as a
  whole) — no individual key is given invented meaning, since SARIF does
  not standardize what a producer puts in there.
- A rule's `helpUri`, when present, becomes a reference.
- Multiple `runs` are all processed; each run's rules are indexed
  independently (a `ruleIndex` in run 2 has nothing to do with run 1's
  rules).

**Not extracted** (out of scope for Threnda's current finding model, but
preserved by keeping the whole `result` object in `raw_data`): suppressions,
related locations, code flows, fixes, and multi-location results beyond the
first location.

Example: `demo-data/sarif/synthetic-scan.sarif.json` — two runs, explicit
and rule-default severity, `ruleIndex` lookup, a `partialFingerprints`
entry, a help link, and a `pass`-kind result to show it's excluded.

## Pentest report JSON

Threnda's own structured shape for a pentest report that already exists as
JSON — deliberately a different top-level shape from generic JSON (a
`"report"` object and a plural `"affected_assets"` array, not a singular
`"asset"`), so `detect()` cleanly rejects a generic-JSON-shaped upload
instead of silently reinterpreting it:

```json
{
  "report": {"title": "Synthetic Web App Pentest — Q1 2026"},
  "findings": [
    {
      "id": "PENTEST-001",
      "title": "SQL Injection in login form",
      "severity": "high",
      "cvss": 8.1,
      "cve": "CVE-2024-12345",
      "affected_assets": [
        {"key": "host:app01.example.test", "name": "app01.example.test", "type": "host"}
      ],
      "description": "...",
      "steps_to_reproduce": "...",
      "evidence": "...",
      "remediation": "...",
      "exploit_evidence": true,
      "references": ["https://example.test/advisory"]
    }
  ]
}
```

- `title` is required. Either `severity` or `cvss` is required, exactly like
  generic JSON.
- **Fan-out**: one `NormalizedFinding` is produced *per affected asset*, with
  `source_finding_id = f"{id}::{asset_key}"` — a pentest finding routinely
  affects several hosts, and each asset-specific instance gets independent,
  idempotent identity through the same Milestone 3 differential/correlation
  engine every other importer uses. Re-importing the same report produces
  `UNCHANGED`, not duplicates. A finding with no `affected_assets` given
  produces a single `NormalizedFinding` keyed on the bare `id`.
- `steps_to_reproduce` (a field with no equivalent `Finding` column) is
  folded into `evidence`, labelled, rather than adding a pentest-specific
  column.
- `report.title` is validated (must be a string, if present) but not
  persisted anywhere new.
- `affected_assets[].type` falls back to `unknown` for an unrecognized type,
  exactly like generic JSON's `asset.type`.

Example: `demo-data/pentest/synthetic-pentest-report.json` — a fan-out
finding across two hosts, a single-asset finding, and a CVSS-only finding
with no explicit severity label.

Uploading an *unstructured* PDF/TXT/Markdown pentest report — one that
isn't already this JSON shape — is a different, local-only flow requiring
human review of every extracted candidate before it becomes a `Finding`;
see `docs/pentest-and-ai.md`.

## Deliberately out of scope for Milestone 2

- Cross-import deduplication and the differential engine (`NEW` /
  `UNCHANGED` / `UPDATED` / `RESOLVED` / `REOPENED`) — Milestone 3.
  `Finding.fingerprint` is populated now (see
  `threnda/app/services/ingestion/fingerprint.py`) but is a provisional,
  per-importer value; nothing yet correlates findings across imports into
  an `Exposure`.
- User authentication — `workspace_id` remains caller-supplied, as in every
  other Milestone 1/2 endpoint. **Superseded by Milestone 7**: every import
  endpoint now requires an authenticated session with at least the
  `analyst` role in the target workspace — see `docs/security.md`,
  "Authorization".
- The browser UI — this is an API-only milestone; provisioning a workspace
  and source, and uploading files, is done through the documented HTTP API
  (see `README.md` and the OpenAPI docs at `/docs`), not a web form.
- Trivy, Nuclei, Semgrep and vendor (Qualys, Tenable, …) importers — the
  `Importer` interface is designed for them, but only CSV/JSON/SARIF shipped
  at Milestone 2 (structured pentest report JSON was added in Milestone 6 —
  see "Pentest report JSON" above).
