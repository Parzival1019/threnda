# Threnda

**Every finding. One queue.**

Threnda is an open-source, self-hosted **security finding management and
exposure management** platform. It takes the output of the scanners, testers
and reports you already run, turns them into one consistent finding model, and
puts the result into a single actionable queue that a security team can
actually work through.

> **Status: early development / pre-alpha.**
> Milestones 1 (project foundation), 2 (ingestion), 3 (deduplication and
> exposures), 4 (workflow and risk scoring), 5 (web UI) and 6 (pentest
> ingestion, optional local AI) are implemented and pending owner review:
> domain models, SQLite persistence with Alembic migrations,
> workspace/source provisioning, CSV/JSON/SARIF/structured-pentest-JSON file
> upload, local (no-cloud, no-OCR) PDF/TXT/Markdown pentest report
> extraction with a mandatory-human-review candidate-finding queue,
> optional local Ollama AI assistance (disabled by default, zero network
> calls when disabled), layered deduplication/correlation with a
> manual-review queue for uncertain matches, an import differential engine
> (NEW/UNCHANGED/UPDATED/RESOLVED/REOPENED), server-enforced exposure
> lifecycle transitions, a deterministic explainable risk score (0-100), a
> local-development REST API, and a server-rendered Jinja2 + HTMX UI
> (findings queue with risk filtering, exposure detail with a score
> breakdown, imports, sources, document review, review queue, dashboard)
> all exist and are tested. There is still no authentication (Milestone 7)
> — the web UI's workspace selection and CSRF protection are both plain
> browsing conveniences, not access control. See
> [docs/implementation-plan.md](docs/implementation-plan.md) for exact
> scope and known limitations, [docs/risk-scoring.md](docs/risk-scoring.md)
> for the scoring engine, [docs/deduplication.md](docs/deduplication.md)
> for the correlation/differential design, and
> [docs/pentest-and-ai.md](docs/pentest-and-ai.md) /
> [docs/local-ai.md](docs/local-ai.md) for the pentest ingestion and
> optional AI provider. Do not deploy this, and do not treat anything below
> as a description of finished, production-ready functionality.

## Local development

Requires Python 3.12+. There is no Docker packaging yet (Milestone 7).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Apply the database schema (SQLite by default, see .env.example — the
# default THRENDA_DATABASE_URL points at ./data/threnda.db, and SQLite
# will not create that directory for you)
mkdir -p data
alembic upgrade head

# Run the app (binds to localhost only)
uvicorn threnda.app.main:app --host 127.0.0.1 --port 8080
# -> http://127.0.0.1:8080/ (web UI, redirects to onboarding on a fresh database)
# -> http://127.0.0.1:8080/api/v1/health, http://127.0.0.1:8080/docs (JSON API)

# Tests, lint, types, dependency sanity
pytest
ruff check .
ruff format --check .
mypy threnda tests
pip check
```

### Importing a synthetic finding

Through the web UI: open `http://127.0.0.1:8080/`, create a workspace, add a
source under **Sources**, then **New import** and upload one of the
synthetic samples in `demo-data/`.

Through the HTTP API directly — create a workspace and a source, then upload
one of the synthetic samples in `demo-data/` — see
[docs/importers.md](docs/importers.md) for the full CSV/JSON/SARIF/pentest-JSON
reference and every endpoint's exact contract:

```bash
curl -s -X POST localhost:8080/api/v1/workspaces \
  -H "Content-Type: application/json" -d '{"name": "demo"}'
# -> note the returned "id" as WORKSPACE_ID

curl -s -X POST "localhost:8080/api/v1/sources?workspace_id=$WORKSPACE_ID" \
  -H "Content-Type: application/json" -d '{"name": "demo-json", "source_type": "json"}'
# -> note the returned "id" as SOURCE_ID

curl -s -X POST "localhost:8080/api/v1/imports?workspace_id=$WORKSPACE_ID" \
  -F "source_id=$SOURCE_ID" \
  -F "file=@demo-data/json/synthetic-findings.json;type=application/json"
```

### Reviewing a synthetic pentest PDF/text report

A source with `source_type: pentest_report` accepts either structured JSON
(via `/api/v1/imports`, same as above) or a PDF/TXT/Markdown file (via
`/api/v1/documents`), extracted locally with every result requiring an
explicit human accept before it becomes a finding — see
[docs/pentest-and-ai.md](docs/pentest-and-ai.md):

```bash
curl -s -X POST "localhost:8080/api/v1/sources?workspace_id=$WORKSPACE_ID" \
  -H "Content-Type: application/json" \
  -d '{"name": "demo-pentest", "source_type": "pentest_report"}'
# -> note the returned "id" as SOURCE_ID

curl -s -X POST "localhost:8080/api/v1/documents?workspace_id=$WORKSPACE_ID" \
  -F "source_id=$SOURCE_ID" \
  -F "file=@demo-data/documents/synthetic-pentest-report.txt;type=text/plain"
# -> inspect the returned "candidates"; note one "id" as CANDIDATE_ID

curl -s -X POST "localhost:8080/api/v1/documents/candidates/$CANDIDATE_ID/accept?workspace_id=$WORKSPACE_ID" \
  -H "Content-Type: application/json" -d '{"severity": "high"}'
```

## Vision

```
Security Sources
      ↓
   Ingest
      ↓
 Normalize
      ↓
 Correlate
      ↓
 Deduplicate
      ↓
 Prioritize
      ↓
 One actionable queue
```

Most teams do not have a finding shortage. They have the same finding reported
five times, in four formats, by three tools, with no owner and no agreed
priority. Threnda's goal is to collapse that into one queue.

## Community principles

- **Self-hosted** — you run it, on your own infrastructure
- **Privacy-first** — your findings are your findings
- **Local-first** — the default deployment is a single machine
- **Offline-capable** — no internet connection required to operate
- **Vendor-neutral** — no preferred scanner, no preferred cloud
- **Unlimited findings** — no artificial caps
- **Unlimited assets** — no artificial caps
- **No mandatory cloud services**
- **No mandatory external AI** — local AI is optional, never required
- **No telemetry by default**

## Editions

**Threnda Community** (this repository) is intended to be genuinely useful on
its own, not a demo of a paid product. See [CLAUDE.md](CLAUDE.md) for the
documented Community/Enterprise boundary.

**Threnda Enterprise** is a separate private repository for future commercial
extensions (live vendor integrations, SSO/SAML/SCIM, advanced RBAC, HA,
BYOC and air-gapped deployment, enterprise AI orchestration, premium support
tooling). None of it is implemented yet.

## Clean-room notice

Threnda is an **independently developed clean-room project**.

Development may use only:

- public documentation
- public standards
- open-source information
- synthetic test data
- code authored specifically for Threnda

Development must **never** use or reproduce:

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

This rule applies permanently to this repository and to every contribution
made to it.

## License

**TODO: License selection is pending before the first public release.**

No license has been chosen yet. Until a `LICENSE` file exists in this
repository, no open-source license is granted — do not assume MIT, Apache,
GPL or any other license applies.

## Contributing & security

- [CONTRIBUTING.md](CONTRIBUTING.md) — how to contribute, and the clean-room
  requirement that applies to every contribution
- [SECURITY.md](SECURITY.md) — how to report a vulnerability privately
