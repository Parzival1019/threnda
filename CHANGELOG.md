# Changelog

All notable changes to Threnda Community are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project uses semantic versioning.

## [Unreleased] — pilot readiness

### Added

- Clients and client projects; consultancy admins (`is_org_admin`).
- Client user role bound to one client, a client portal, and server-side
  isolation of findings, comments, files, history, reports and exports.
- One-time invitation links (hashed, expiring, revocable, redacted from logs).
- Manual finding entry and editing (title, description, severity, affected
  system, remediation, owner, due date) with full history.
- Pentest findings CSV template: downloadable template, all-or-nothing
  validation with a line/column error list, import summary, documented
  identity rules so re-imports never duplicate.
- "Fixed — awaiting retest" status, retests, and verified closure that is
  only possible through a passed retest; failed retests reopen.
- Comments (internal or client-visible) and evidence attachments (type and
  size limits, stored in the database, audited downloads).
- All-projects overview, per-project status page, printable client status
  report; CSV export gains affected system and description.
- Fictional, resettable demo dataset (`threnda demo reset`,
  `docker-compose.demo.yml`) guarded by demo mode and a database marker.
- `threnda backup` / `threnda restore` for SQLite.
- Hungarian pilot/sales pack and a landing page under `docs/pilot/` and `site/`.

### Changed

- A finding can no longer be set to `resolved` by a plain status change
  (HTTP 409); use a retest.
- Creating workspaces/projects requires a consultancy admin.
- A user with no project is sent to `/overview` instead of `/workspaces/new`.

## [0.1.0] - 2026-09-13

### Added

- CSV mapping, JSON, SARIF 2.1.0 and structured pentest-report imports.
- Deterministic and fuzzy deduplication with mandatory review for uncertain
  matches, plus import differential tracking.
- A unified exposure queue, lifecycle history and explainable 0–100 risk
  scoring.
- Server-rendered web UI, dashboard, filtering, bulk actions and CSV/JSON
  exposure export.
- Local accounts, Argon2id password hashing, secure sessions, workspace roles,
  CSRF protection, lockout/rate limiting and administrative audit logs.
- Local PDF/TXT/Markdown extraction with a human-reviewed candidate queue.
- Self-contained Local AI using checksum-pinned llama.cpp and Qwen2.5 1.5B
  Instruct Q4_K_M; no separate Ollama or cloud account required.
- PostgreSQL-backed Docker Compose deployment, health checks, migrations,
  backup/restore guidance and a generic SSRF-defended webhook.
- Offline Local AI bundle creation and installation commands.

### Security

- Upgraded `pypdf` to `6.16.1` or newer after dependency auditing identified
  vulnerabilities in the previous 5.x line.
- Added the Linux OpenMP runtime required by the bundled llama.cpp executable.
- Hardened the runtime container by applying distribution security updates,
  removing package-management tooling and verifying zero fixable high/critical
  findings in the release-candidate image.

[0.1.0]: https://github.com/Parzival1019/threnda/releases/tag/v0.1.0
