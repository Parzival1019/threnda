# Pentest findings CSV import

For a consultancy's own pentest results. Scanner exports (Nessus, Burp,
etc.) keep using **Imports** with the mapping wizard — see
[importers.md](importers.md).

- Download the template: **Import findings CSV → Download template**
  (`/findings-import/template.csv`, API: `GET /api/v1/findings-csv/template`).
- Upload: **Import findings CSV** (API: `POST /api/v1/findings-csv?workspace_id=…`).
- Requires the Pentester or Consultancy admin role.

## Columns

| Column | Required | Format |
| --- | --- | --- |
| `id` | no (recommended) | your finding ID, e.g. `PT-001`, max 100 chars |
| `title` | **yes** | max 500 chars |
| `severity` | **yes** | `critical`, `high`, `medium` (or `moderate`), `low`, `info`/`informational` |
| `affected_system` | no | host, URL, IP or system name |
| `description` | no | free text |
| `remediation` | no | free text |
| `owner` | no | person or team |
| `due_date` | no | `YYYY-MM-DD` |
| `cvss` | no | 0.0–10.0 |
| `cve`, `cwe`, `evidence` | no | free text |

Column names are case-insensitive; any order; optional columns can be left
out. The file must be UTF-8 ("CSV UTF-8" in Excel).

## Validation

The whole file is validated first. If **anything** is wrong, nothing is
imported and every problem is listed with line number (header = line 1),
column and a plain-language message — unknown/duplicated/missing columns,
empty title, unknown severity, invalid date or CVSS, too-long values, rows
with extra values, duplicate rows (same `id`, or same title + affected
system + CVE when there is no `id`), non-UTF-8 files. After a successful
import the page shows: rows, new, updated, unchanged, back in the report,
and previously imported findings not in this file.

## Identity and re-import (no duplicates)

1. With an `id`, the id is the finding's identity in the project. Re-importing
   it updates the same finding even if the title or severity changed.
2. Without an `id`, identity is title + affected system + CVE
   (case/whitespace-insensitive). Changing one of those three in the file
   looks like a new finding — use `id` to avoid that.

On re-import:

- report content (title, description, severity, affected system,
  remediation) follows the file; each change appears in the history as
  *Details changed* by "<user> (import)";
- owner and due date are only filled in when still empty in Threnda — edits
  made in Threnda are never overwritten;
- the status is never changed by an import. A finding that is no longer in
  the file is marked *not in latest import* and keeps its status; only a
  retest closes a finding;
- a finding that comes back after being absent is marked *reappeared*.

Imported findings are never merged with other findings automatically (each
row gets its own finding); scanner imports keep their correlation engine.
