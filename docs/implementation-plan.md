# Threnda Community — implementation plan

This document tracks actual delivery against `docs/community-v0.1-spec.md`. It
is updated at the end of every milestone. Status values are `not started`,
`in progress` or `done`. "Done" means implemented, tested and reviewed by the
owner — not merely written.

Nothing in this file is a claim about production readiness. See CLAUDE.md and
the README status banner for the authoritative project status.

## Milestone 1 — project foundation

**Status: done (implementation), pending owner review**

Scope: application skeleton, configuration, domain models, database,
migrations, a small set of read-mostly local-development APIs, and tests.
No ingestion, no UI, no authentication.

Acceptance criteria:

- [x] FastAPI app factory (`create_app()`) with settings loaded from
      environment variables (`THRENDA_*`), matching `.env.example`.
- [x] SQLAlchemy 2 models for `Workspace`, `Source`, `ImportRun`, `Asset`,
      `Finding`, `Exposure`, `ExposureHistory`, all workspace-scoped, all
      timestamps timezone-aware UTC.
- [x] SQLite persistence by default, foreign keys enforced (`PRAGMA
      foreign_keys=ON`), schema written to be PostgreSQL-compatible (no
      SQLite-only column types; enums stored as `VARCHAR` + `CHECK`, JSON via
      the SQLAlchemy-generic `JSON` type).
- [x] Alembic migrations, generated from the models and verified with an
      upgrade/downgrade round trip against a temporary database.
- [x] Pydantic v2 schemas for API request/response bodies.
- [x] Versioned local-development API:
      `GET /api/v1/health`,
      `GET /api/v1/exposures`, `GET /api/v1/exposures/{id}`,
      `PATCH /api/v1/exposures/{id}`,
      `GET /api/v1/imports`, `GET /api/v1/sources`, `GET /api/v1/assets`.
      All list endpoints are paginated (`limit`/`offset`) and scoped by an
      explicit `workspace_id` query parameter.
- [x] `PATCH /api/v1/exposures/{id}` writes `ExposureHistory` audit rows for
      every meaningful change (status, owner, acknowledged, remediation
      target date, note) — the "sound foundation for updates" asked for in
      the brief, not a stub.
- [x] Tests: model/integrity, migration round-trip, config, and API tests,
      all against temporary SQLite databases and synthetic fixtures.
- [x] `ruff check`, `ruff format --check`, `mypy` clean for `threnda/`.
- [x] Local commit on `feat/community-v0.1`. Not pushed.

### Design decisions made in this milestone

- **`metadata` naming collision.** `Source.metadata` and `Asset.metadata`
  from the spec collide with `DeclarativeBase.metadata`, which SQLAlchemy
  reserves on every mapped class. The Python attribute (and the JSON API
  field) is called `meta`; the underlying database column is still literally
  named `metadata`, so the schema still matches the spec's field list.
- **Exposure/Finding relationship.** Implemented as one-to-many
  (`Finding.exposure_id` nullable FK to `Exposure.id`) rather than a
  many-to-many join table. The spec allows either; a finding being
  correlated into more than one exposure at once isn't a real scenario here,
  and the simpler shape avoids an unneeded association table.
- **`Finding.import_run_id`.** Added beyond the spec's minimum field list
  (nullable FK to `ImportRun`). It costs nothing now and is required for the
  import differential engine in Milestone 3 — without it there is no way to
  tell which import last observed a given finding.
- **Workspace scoping without auth.** There is no session/auth yet, so
  `workspace_id` is an explicit required query parameter on every list/detail
  endpoint rather than derived from an authenticated user. Every lookup
  filters on it, and a resource that exists but belongs to a different
  workspace returns `404`, not `403` (it must not reveal existence across
  workspace boundaries). This is a placeholder to be replaced by
  session-derived scoping when authentication lands in Milestone 7 — it is
  not itself an access control mechanism, since anyone with API access today
  can pass any `workspace_id`. Documented as a limitation below.
- **Sessions per app, not a global.** `create_app()` builds its own engine
  and sessionmaker and stores them on `app.state`, instead of a
  module-global engine. This is what lets tests spin up an isolated SQLite
  file per test without monkeypatching global state.
- **Enums stored as `VARCHAR`, not native `ENUM`.** SQLite has no native enum
  type and Postgres's `CREATE TYPE ENUM` is awkward to migrate (adding a
  value requires special handling). `sqlalchemy.Enum(..., native_enum=False)`
  stores plain lowercase string values with a `CHECK` constraint, which
  behaves identically on both databases.
- **Timestamps.** All `datetime` columns are `DateTime(timezone=True)` with a
  Python-side default of `datetime.now(UTC)`, not a database server default —
  server-side `now()` differs between SQLite and PostgreSQL, and a Python
  default keeps behaviour identical on both.
- **No detail/POST endpoints yet for imports/sources/assets.** The spec's
  full API list includes `POST /api/v1/imports`, which is ingestion and
  explicitly out of scope until Milestone 2 (there is nothing to import
  yet). Per-resource `GET /{id}` for sources/imports/assets was not requested
  by the Milestone 1 brief and is deferred; only the exposure detail route
  was required for the update workflow.

### Known limitations after Milestone 1

- No authentication. `workspace_id` is caller-supplied. Do not expose this
  build beyond local development.
- No ingestion. There is no way to get data in except by writing directly to
  the database (which the tests do, using synthetic fixtures).
- No UI.
- No risk scoring logic yet — `Exposure.risk_score` exists and defaults to
  `0`; the rule engine is Milestone 4.
- No deduplication/correlation logic yet — the `Finding.exposure_id` link
  exists but nothing populates it automatically.
- `docker-compose.yml` / `Dockerfile` do not exist yet (Milestone 7).

## Milestone 2 — ingestion framework

**Status: not started**

Scope: generic importer interface (`detect`/`validate`/`parse`/`normalize`),
CSV importer with a column-mapping wizard, generic JSON importer, SARIF 2.1.0
importer, synthetic demo datasets for each, `POST /api/v1/imports`.

Acceptance criteria:

- [ ] `Importer` protocol/base class with the four-stage interface.
- [ ] CSV importer: column mapping (title/asset/severity/CVSS/CVE/description
      /remediation), severity name normalization (informational..critical),
      mapping persisted per source where practical.
- [ ] Generic JSON importer for a documented Threnda-native finding shape.
- [ ] SARIF 2.1.0 importer: rule ID, message, severity, location,
      fingerprints where present, help/remediation links. No assumptions
      tied to one SARIF-producing tool.
- [ ] `POST /api/v1/imports` accepts a file upload, creates an `ImportRun`,
      and persists normalized `Finding` rows against it.
- [ ] Synthetic demo data: generic CSV, SARIF sample, and a second CSV/JSON
      export of the same source demonstrating repeat-import behaviour (used
      fully once Milestone 3's differential engine exists).
- [ ] Tests: CSV mapping, SARIF parsing, JSON normalization, upload
      validation (size limits, content-type, malformed input).

## Milestone 3 — deduplication and exposures

**Status: not started**

Scope: fingerprinting, layered deduplication (deterministic + fuzzy),
exposure correlation, import differential engine (`NEW`/`UNCHANGED`/
`UPDATED`/`RESOLVED`/`REOPENED`).

Acceptance criteria:

- [ ] Deterministic fingerprint generator using the available combination of
      CVE/CWE/asset identity/URL/rule ID/repo+file/image digest/normalized
      title, documented and stable across re-imports of unchanged data.
- [ ] Fuzzy-matching layer for `POSSIBLE` confidence duplicates using a
      deterministic text-similarity algorithm (no new heavy dependency).
- [ ] `EXACT`/`HIGH` confidence matches correlate automatically into an
      `Exposure`; `POSSIBLE` matches are surfaced for manual review
      (merge / keep separate), never auto-merged.
- [ ] Import differential engine comparing a new `ImportRun` against the
      prior state of a `Source` and classifying each finding.
- [ ] Historical findings are never deleted; `RESOLVED` findings/exposures
      are marked, not removed.
- [ ] Tests: fingerprint stability, each confidence tier, differential
      classification against the synthetic repeat-import fixture from
      Milestone 2.

## Milestone 4 — workflow, history, risk scoring

**Status: not started**

Scope: full exposure lifecycle enforcement, explainable rule-based risk
scoring, richer API.

Acceptance criteria:

- [ ] Lifecycle transitions enforced server-side (`OPEN` → `ACKNOWLEDGED` →
      `IN_PROGRESS` → `RESOLVED`/`RISK_ACCEPTED`, with `REOPENED` handling).
- [ ] Rule-based risk score (0-100) from severity, CVSS, asset environment,
      internet exposure (if known), exploit evidence (if known), finding
      age, and manual business criticality — each contributing an
      explainable, labelled delta.
- [ ] `GET /api/v1/exposures/{id}` includes the score breakdown.
- [ ] Rules configurable in code, structured so they can move to
      user-configuration later without a rewrite.
- [ ] Tests: each scoring input, transition validity/invalidity, score
      explanation output.

## Milestone 5 — web UI

**Status: not started**

Scope: Jinja2 + HTMX UI — findings list, exposure detail, imports, sources,
settings, minimal dashboard.

Acceptance criteria:

- [ ] Findings/exposures list: search, severity/source/owner/status filters,
      risk range, sorting, pagination.
- [ ] Exposure detail: full field set from the spec, action buttons
      (acknowledge, assign owner, set target date, change status, add note,
      review possible duplicates).
- [ ] Imports list and import detail with visible error reporting.
- [ ] Sources list.
- [ ] Minimal dashboard (open/critical/high/overdue/new this week/resolved
      this week/average remediation age) — deliberately not a visualization
      showpiece.
- [ ] CSRF protection on all state-changing forms; HTML escaping verified.
- [ ] Tests: route rendering, filter behaviour, CSRF enforcement.

## Milestone 6 — pentest ingestion, optional local AI

**Status: not started**

Scope: structured pentest JSON import, deterministic local PDF/text
extraction and candidate-finding framework, `AIProvider` abstraction with
`DisabledAIProvider` and `OllamaAIProvider`.

Acceptance criteria:

- [ ] Structured pentest JSON importer.
- [ ] Local, deterministic PDF/text extraction with section detection and a
      candidate-finding extraction framework; no external OCR or AI API
      calls; manual conversion of candidates to findings works with AI
      disabled.
- [ ] `AIProvider` interface: `summarize_finding`, `explain_finding`,
      `suggest_remediation`, `extract_findings_from_text`,
      `compare_findings`.
- [ ] `DisabledAIProvider` (default) and `OllamaAIProvider`
      (`THRENDA_AI_PROVIDER=ollama`, `THRENDA_AI_MODEL=<model>`), calling a
      local Ollama instance only — no cloud LLM calls anywhere in the
      application. Threnda continues functioning normally if Ollama is
      unreachable.
- [ ] AI output is visibly marked as machine-generated and never silently
      modifies a security record.
- [ ] Tests: pentest JSON import, PDF text extraction on a synthetic sample,
      `DisabledAIProvider` behaviour, `OllamaAIProvider` against a mocked
      HTTP layer (no real Ollama dependency in CI).

## Milestone 7 — authentication, hardening, packaging, docs

**Status: not started**

Scope: local accounts, security hardening, Docker packaging, complete
documentation, final test pass.

Acceptance criteria:

- [ ] Local username/password accounts, secure password hashing, secure
      session cookies; `workspace_id` scoping in Milestone 1's API replaced
      by session-derived scoping.
- [ ] CSRF protection, upload size limits, safe filenames, content
      validation, no unsafe deserialization, no arbitrary shell execution.
- [ ] Administrative changes audited; sensitive raw findings not logged
      unnecessarily.
- [ ] `Dockerfile` and `docker-compose.yml`: `docker compose up -d` then
      `http://localhost:8080` with no manual database setup.
- [ ] CSV/JSON export endpoints; generic outbound webhook mechanism
      (delivery only — no vendor-specific integration).
- [ ] `docs/architecture.md`, `docs/data-model.md`, `docs/importers.md`,
      `docs/deduplication.md`, `docs/local-ai.md`, `docs/security.md`,
      `docs/roadmap.md`; README quick start reproducible from a clean clone.
- [ ] Full test suite green; the Definition of Done in
      `docs/community-v0.1-spec.md` walked through end to end.
