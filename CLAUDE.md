# CLAUDE.md — Threnda

Guidance for Claude Code sessions working in this repository.

## Product

- **Name:** Threnda
- **Edition in this repository:** Threnda Community
- **Tagline:** Every finding. One queue.
- **What it is:** an open-source, self-hosted security finding management and
  exposure management platform.
- **Status:** Community v0.1 release candidate with Desktop packaging in
  progress. Do not describe unimplemented or unaudited behaviour as if it
  works, in code comments, docs, commit messages or the UI.

## Clean-room rules (permanent, non-negotiable)

Threnda is an **independently developed clean-room project**.

Permitted sources of information:

- public documentation
- public standards
- open-source information
- synthetic test data
- code authored specifically for Threnda

Never use or reproduce:

- employer source code
- customer source code
- proprietary schemas
- internal API responses
- internal documentation
- non-public workflows
- credentials
- confidential security findings
- internal hostnames
- real customer data
- proprietary business logic

If a task appears to require any of the above, stop and say so instead of
improvising something that resembles it.

Related working rules:

- **Never fabricate vendor APIs.** If a vendor's API shape is not in public
  documentation you have actually read, do not guess it.
- **Never assume proprietary API responses.** Do not invent response payloads
  from memory of a non-public system.
- **Use official public documentation** when building future integrations.
- **Use synthetic fixture data** for every test and every demo dataset. No
  real hostnames, IPs, customer names, or real findings — ever, including in
  `demo-data/`.
- **Write tests before considering a feature complete.**
- **Keep Community genuinely useful.** Community is a real product, not a
  crippled trial.
- **Do not introduce artificial asset or finding limits.**

## Architecture principles

Start as a **modular monolith**. Split things out only when there is a
concrete, demonstrated reason.

- Python 3.12+
- FastAPI
- SQLAlchemy 2
- Pydantic v2
- Alembic for migrations
- SQLite by default
- PostgreSQL-compatible architecture (no SQLite-only constructs in the schema)
- Jinja2 + HTMX frontend
- Minimal JavaScript
- Desktop-first for individual macOS use; Docker Compose for a shared server
- No Kubernetes requirement for Community
- Optional local AI only
- No external LLM requirement
- No telemetry by default

### Do not introduce without strong justification

Kafka · Elasticsearch · RabbitMQ · Neo4j · Redis · Kubernetes ·
microservice architecture · a separate vector database

"It would scale better later" is not strong justification. Bring a concrete
problem that the current stack has actually failed to solve.

### Target development machine

An Apple Silicon MacBook with **16 GB RAM**. The whole application — API,
database, worker, frontend — must run comfortably there alongside an editor
and a browser. Keep it lightweight. A dependency that pulls in a JVM, a
multi-gigabyte model, or a multi-container orchestration requirement is a
design failure for Community.

## Community / Enterprise boundary

This is the intended high-level split. Many Community items below are now
implemented; check the current-state section before claiming exact behavior.

**Threnda Community will eventually include:**

- persistent local database
- CSV ingestion
- JSON ingestion
- SARIF ingestion
- pentest report ingestion
- common finding model
- exposure model
- finding history
- basic correlation
- deduplication
- deterministic risk scoring
- remediation workflow
- manual ownership
- local accounts
- local optional AI
- REST API
- CSV/JSON export
- a Docker-free macOS desktop app for individual use
- a shared Server mode with local accounts and basic workspace roles

**Future commercial (Enterprise) functionality may include:**

Live vendor integrations — Qualys, Red Hat ACS, Rapid7, Pentera, Tenable,
Wiz, Snyk, ServiceNow, advanced Jira, Splunk.

Platform capabilities — advanced correlation, security knowledge graph,
attack-path analysis, automated ownership, CMDB enrichment, SSO, SAML, OIDC,
SCIM, advanced RBAC, enterprise auditing, HA, BYOC, AWS/Azure/GCP Terraform
deployment, OpenShift/Kubernetes enterprise deployment, air-gapped update
management, advanced private AI orchestration, SLA and premium support.

Enterprise functionality lives in the separate private
`Parzival1019/threnda-enterprise` repository. **Do not implement it here**,
and do not copy Community source code into that repository.

## Repository layout

```
threnda/
├── .github/
│   ├── ISSUE_TEMPLATE/
│   └── workflows/
├── docs/
├── demo-data/        synthetic datasets only
├── migrations/       Alembic environment and versions
├── tests/
├── threnda/          application package
├── .env.example      placeholders only, never real secrets
├── .gitignore
├── alembic.ini
├── CLAUDE.md
├── CONTRIBUTING.md
├── SECURITY.md
├── README.md
└── pyproject.toml
```

## Licensing

No license has been chosen. Do not add a `LICENSE` file, and do not describe
the project as MIT, Apache, GPL or anything else until the owner decides.

## Current state

Milestones 1 (project foundation), 2 (ingestion), 3 (deduplication and
exposures), 4 (workflow and risk scoring), 5 (web UI) and 6 (pentest
ingestion, optional local AI) are implemented and pending owner review:
app factory/configuration, SQLAlchemy 2 models for the full domain
(Workspace, Source, ImportRun, Asset, Finding, Exposure, ExposureHistory,
PossibleDuplicate, Document, CandidateFinding) with workspace-scoped
composite foreign keys and DB-enforced enums, SQLite persistence with
foreign keys enforced, Alembic migrations verified with upgrade/downgrade
round trips, Pydantic v2 schemas, and a local-development API:
`/api/v1/health`, `/api/v1/workspaces`, `/api/v1/sources` (incl.
creation), `/api/v1/exposures` (incl. `PATCH`, server-enforced lifecycle
transitions, and a risk-score breakdown on the detail route),
`/api/v1/imports` (incl. file upload), `/api/v1/assets` (incl. `PATCH`
for the two manual risk-scoring fields), `/api/v1/possible-duplicates`
(list/merge/keep-separate), `/api/v1/documents` (incl. file upload and
candidate accept/reject). CSV, generic JSON, SARIF 2.1.0 and structured
pentest-report JSON file uploads work end to end through the real HTTP
API and run through a layered deduplication/correlation engine plus an
import differential engine (NEW/UNCHANGED/UPDATED/RESOLVED/REOPENED). A
deterministic, explainable, rule-based risk-scoring engine
(`threnda/app/services/risk_scoring.py`, no AI) scores every exposure
0-100 from severity/CVSS/asset environment/internet exposure/exploit
evidence/business criticality/finding age. Local, fully offline PDF/TXT/
Markdown pentest report extraction (`threnda/app/services/documents/`, no
OCR, no network calls) proposes candidate findings that always require an
explicit human accept/reject decision before becoming a real finding —
optionally assisted by Threnda's managed llama.cpp/Qwen local-AI bundle
(`threnda/app/services/ai/`), with no separate AI application or cloud
provider required. A server-rendered Jinja2 + HTMX web UI
(`threnda/app/web/`) covers the findings queue (with risk-score
filtering) with its exposure detail panel (including a Risk tab), imports
(including a CSV column-mapping wizard), sources, document upload and
candidate review, the possible-duplicate review queue, and a minimal
dashboard, with CSRF protection (double-submit cookie) on every
state-changing form. See docs/implementation-plan.md for exact scope,
decisions and known limitations, docs/importers.md for the ingestion
contract, docs/deduplication.md for the correlation/differential design,
docs/risk-scoring.md for the scoring engine, and docs/pentest-and-ai.md /
docs/local-ai.md for the pentest ingestion and optional AI provider.

Milestone 7 (authentication, hardening, packaging, docs) is implemented and
pending owner review: local username/password accounts (Argon2id via
`argon2-cffi`, a baseline password policy, constant-time-shaped login
against unknown usernames, per-account lockout plus a process-local
per-IP rate limit), opaque high-entropy sessions stored only as a SHA-256
hash (`threnda/app/models/user_session.py`) with idle/absolute expiry and
rotation on login, per-workspace roles (admin/analyst/viewer,
`threnda/app/models/workspace_membership.py`) that are now the sole
authority for every workspace-scoped API and web route — a caller-supplied
`workspace_id` alone no longer grants anything, and a workspace that
doesn't exist and one the caller isn't a member of both answer `404`
identically. Session-bound CSRF tokens (`threnda/app/web/csrf.py`) replace
the earlier double-submit-cookie placeholder, baseline response security
headers (CSP/HSTS-adjacent headers via
`threnda/app/core/middleware.py::SecurityHeadersMiddleware`) are attached
to every response, and an administrative audit log
(`threnda/app/models/audit_log.py`, single write path
`threnda/app/services/audit.py`) records account/membership/webhook/export
events while never logging a password, session token, or webhook secret.
CSV/JSON export (`threnda/app/services/exports.py`, formula-injection-safe,
streamed in bounded batches, no artificial row cap) and a generic,
admin-triggered, SSRF-defended outbound webhook
(`threnda/app/services/webhooks.py`) are both new. First-admin bootstrap is
explicit and one-time (`threnda bootstrap-admin`, or
`THRENDA_BOOTSTRAP_ADMIN_USERNAME`/`_PASSWORD` in the Docker Compose
entrypoint) and invents no credentials; `threnda/app/core/startup_checks.py`
fails closed at startup if `THRENDA_ENV=production` with a missing, default,
or too-short `THRENDA_SECRET_KEY`. A multi-stage, non-root `Dockerfile` and
a `docker-compose.yml` running PostgreSQL (with a healthcheck, automatic
`alembic upgrade head`, and named volumes) bring the whole stack up with
`docker compose up -d` from a clean checkout given only the one-time
secrets in `.env`. See docs/security.md for the full
authentication/session/CSRF/authorization/audit design, docs/deployment.md
for the Docker/bootstrap/TLS/backup/upgrade operational contract,
docs/webhooks.md and docs/exports.md for those two features, and
docs/implementation-plan.md for exact scope, verification results, and
known limitations.

Milestone 8 (self-contained Local AI) adds a pinned, checksum-verified
llama.cpp/Qwen bundle, `threnda ai install`, offline-bundle support, and an
admin-only Settings page. AI actions remain explicit and advisory, but
ordinary Community users no longer install or configure Ollama separately.

Milestone 9 is in progress: Community now has a tested macOS Desktop launcher,
automatic SQLite migrations, Keychain-backed instance secrets, loopback-only
first-run onboarding, and a locally verified Apple Silicon `.app` build. The
same codebase remains available as Community Server for small teams. The app is
not a public binary until it has a product icon, real Developer ID signing,
Apple notarization and clean-machine verification; see `docs/desktop.md`.

This remains pre-alpha and has not had an independent security review —
see docs/security.md, "What this is not" — but the web UI and JSON API are
no longer unauthenticated: every workspace-scoped route requires a real
session and a real per-workspace role.
