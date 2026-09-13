# CSV/JSON export

`GET /api/v1/exports/exposures?format=csv|json` (analyst role or higher;
workspace-scoped like every other endpoint) streams every exposure in a
workspace. There is no page in the web UI dedicated to exports beyond a
direct link — visit the URL above while logged in and your browser will
download the file using your existing session cookie.

## Schema

One row/object per exposure, in this exact, stable column order:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer | |
| `canonical_fingerprint` | string | |
| `title` | string | |
| `severity` | string | one of `informational`/`low`/`medium`/`high`/`critical` |
| `risk_score` | integer | 0–100, the persisted list-view value (see [risk-scoring.md](risk-scoring.md) for the one staleness caveat this shares with the findings queue) |
| `status` | string | one of `open`/`acknowledged`/`in_progress`/`risk_accepted`/`resolved` |
| `owner` | string or null | |
| `acknowledged` | boolean | |
| `remediation_target_date` | ISO date or null | |
| `remediation` | string or null | |
| `asset` | string | comma-joined distinct asset names across the exposure's findings; empty if none |
| `sources` | string | comma-joined distinct source names |
| `first_seen` | ISO 8601 datetime | |
| `last_seen` | ISO 8601 datetime | |
| `resolved_at` | ISO 8601 datetime or null | |
| `created_at` | ISO 8601 datetime | |
| `updated_at` | ISO 8601 datetime | |

This schema is intentionally flat (no nested findings/history) — it is meant
for spreadsheets and simple downstream tooling, not as a full data dump. Use
the regular `GET /api/v1/exposures/{id}` for the full detail including the
risk-score breakdown, or query the database directly for anything deeper.

## Streaming and bounded memory

Rows are fetched via keyset pagination (`id > last_id`, in batches of 500 —
`threnda.app.services.exports.iter_workspace_exposures`) and streamed to the
client as they're read, rather than materializing the whole export in memory
first. There is **no cap on the number of exposures exported** — CLAUDE.md
rules out artificial finding/asset limits, and this applies to exports too;
"bounded" here means bounded memory per request, not a bounded result size.

## CSV formula-injection safety

Any exported string field that starts with `=`, `+`, `-`, `@`, a tab, or a
carriage return is prefixed with a leading `'` before being written
(`threnda.app.services.exports._csv_safe`) — the standard OWASP CSV
Injection mitigation. Excel/Google Sheets render the value literally instead
of evaluating it as a formula; the leading `'` is invisible in the rendered
cell and the value round-trips back to plain text on re-import elsewhere.
Quoting/escaping of embedded commas, quotes, and newlines is handled by
Python's `csv` module, never hand-built string concatenation.

## Auditing

Every export download is recorded in the administrative audit log
(`AuditEventType.EXPORT_DOWNLOADED`, with the requested format and the
workspace) — see [security.md](security.md), "Auditing".
