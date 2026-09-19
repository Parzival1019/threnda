# Roadmap

This is a directional list, not a commitment with dates. See CLAUDE.md for
the authoritative Community/Enterprise boundary — nothing in the Enterprise
section below is implemented in this repository, and none of it will be:
Enterprise functionality lives in the separate private
`Parzival1019/threnda-enterprise` repository.

## Shipped in Community v0.1 (Milestones 1–8)

- Domain model, SQLite/PostgreSQL persistence, Alembic migrations.
- CSV (with a column-mapping wizard), generic JSON, SARIF 2.1.0, and
  structured pentest-JSON ingestion.
- Layered deduplication (deterministic + fuzzy) with a manual review queue,
  never auto-merging an uncertain match.
- The import differential engine (new/unchanged/updated/resolved/reopened).
- Exposure lifecycle enforcement and a deterministic, explainable risk score.
- A server-rendered Jinja2 + HTMX web UI.
- Local, offline PDF/text extraction with a mandatory-human-review
  candidate-finding queue; a managed llama.cpp/Qwen local-AI bundle with
  one-click installation and no separate AI application.
- Local accounts, Argon2id password hashing, secure sessions, per-workspace
  roles (admin/analyst/viewer), CSRF protection, an administrative audit
  log, CSV/JSON export, a generic (SSRF-defended) outbound webhook, and
  Docker Compose packaging.

## Plausible future Community work

Nothing here is scheduled. Listed because it was raised during design and
explicitly deferred, not because it's promised:

- Complete the macOS Desktop distribution gate: final icon, Developer ID
  signing, notarized DMG, clean-machine testing and published checksums. The
  native launcher and standalone Apple Silicon `.app` build already exist.

- Additional file-based importers (Trivy, Nuclei, Semgrep) — the
  `Importer` protocol is already designed for this; only the format-specific
  parsing is missing.
- A richer webhook event model (per-event delivery with retries) — needs a
  real delivery queue, which is a bigger architectural step than Community's
  "no background worker" design currently takes (see
  [webhooks.md](webhooks.md), "What this is not").
- Multi-factor authentication for local accounts.
- A dedicated standalone asset-management page (today, an asset's two
  manual risk fields are only editable from an exposure's Risk tab).
- Layer 3 (semantic/embedding) deduplication, gated on local AI being
  enabled (see [deduplication.md](deduplication.md)).

## Enterprise-only (not in this repository)

Live vendor integrations (Qualys, Red Hat ACS, Rapid7, Pentera, Tenable,
Wiz, Snyk, ServiceNow, advanced Jira, Splunk); advanced correlation; a
security knowledge graph; attack-path analysis; automated ownership; CMDB
enrichment; SSO/SAML/OIDC/SCIM; advanced RBAC; enterprise auditing; HA;
BYOC; cloud Terraform deployment; OpenShift/Kubernetes enterprise
deployment; air-gapped update management; advanced private AI
orchestration; SLA and premium support.
