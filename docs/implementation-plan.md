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
- **Timestamps.** All `datetime` columns use a custom `UTCDateTime` type
  (`threnda/app/models/types.py`), not a bare `DateTime(timezone=True)`:
  SQLite has no native timezone-aware datetime type, so SQLAlchemy's SQLite
  dialect silently returns a *naive* `datetime` on read even for a column
  declared `timezone=True` — a real bug caught by a test asserting
  `tzinfo is not None` after a commit/refresh round trip. `UTCDateTime`
  normalizes both directions so application code always sees a
  timezone-aware UTC value on either backend. Defaults are Python-side
  (`datetime.now(UTC)`), not a database server default, since server-side
  `now()` differs between SQLite and PostgreSQL.
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

### Independent review fixes (post-Milestone-1, pre-Milestone-2)

An independent review of commit `28856de` (`docs/milestone-1-review.md`,
later extended with a sixth finding) reproduced six real defects. All six
are fixed, each with regression tests, in a separate local commit on top of
the Milestone 1 commit. Record kept here since these fixes changed the
schema, the migration, and the API's validation/audit behaviour in ways
future milestones build on.

1. **Cross-workspace references were accepted.** A `Finding`/`ImportRun`
   could reference a `Source`/`Asset`/`Exposure`/`ImportRun` belonging to a
   *different* workspace than its own `workspace_id`, at the database level
   — plain single-column foreign keys only guarantee the referenced row
   exists, not that it agrees on tenancy. Fixed with **composite foreign
   keys**: every table a `Finding`/`ImportRun` points at now has a
   `UNIQUE(workspace_id, id)` constraint (or, for `ImportRun`,
   `UNIQUE(workspace_id, source_id, id)`), and the referencing columns
   composite-FK against it, e.g. `Finding(workspace_id, source_id) →
   Source(workspace_id, id)`. `Finding.import_run_id` composite-FKs on all
   three of `(workspace_id, source_id, import_run_id)` so a finding's import
   run must belong to the *same source* the finding itself references, not
   merely the same workspace — a follow-up gap found independently after the
   first fix. This is enforced by SQLite/PostgreSQL themselves, including
   against raw SQL, not just ORM-level validation.
   - **`SET NULL` dropped for the nullable links** (`asset_id`,
     `exposure_id`, `import_run_id`): SQLite's `ON DELETE SET NULL` on a
     composite key nulls *every* column in it, including `workspace_id`,
     which is `NOT NULL` on `findings` — so it would fail its own
     constraint. These now use the database default (no action /
     RESTRICT-like): deleting an `Asset`/`Exposure`/`ImportRun` that a
     `Finding` still references is blocked until the reference is cleared at
     the application level. Safer anyway for a tool whose job is to not lose
     evidence, and nothing in Community deletes those rows yet regardless.
   - **Silent workspace rewrite via ORM relationship assignment.** A second,
     subtler form of the same bug: even with the composite FKs in place,
     `finding.source = some_other_workspace_source` would silently overwrite
     `finding.workspace_id` to match, because SQLAlchemy's default
     many-to-one sync writes every column in a relationship's join
     condition. Fixed with explicit `primaryjoin` + `foreign()` on
     `Finding.source`/`.import_run`/`.asset`/`.exposure` and
     `ImportRun.source` (and their reverse collections), so each
     relationship manages only its own id column — `workspace_id` stays
     writable solely through the `...→.workspace` relationship. Assigning a
     cross-workspace object now still fails, but loudly (`IntegrityError`
     from the composite FK), never silently.
   - **Migration**: `f6319dd94ee1_enforce_workspace_scoped_composite_fks_...py`.
     Rather than rely on Alembic's autogenerated FK-violation handling
     mid-migration (SQLite's batch-mode `ALTER TABLE` rebuilds tables via a
     copy-and-rename dance; a violation partway through that sequence across
     six related tables did not roll back cleanly — a `_alembic_tmp_*` table
     was observed left behind even though real data was untouched), the
     migration validates all of this with plain read-only `SELECT`s
     **before any DDL runs**. A database that fails validation has had
     literally zero schema or data changes made to it, with one error
     message naming every problem found. Verified with a real Alembic
     upgrade: valid linked data survives the upgrade byte-for-byte;
     legacy cross-workspace data blocks the upgrade entirely (revision
     un-advanced, data untouched) rather than being silently dropped or
     rewritten.
2. **Null `PATCH` values crashed the exposures endpoint (HTTP 500).**
   `{"status": null}` or `{"acknowledged": null}` reached the ORM as `None`
   on a `NOT NULL` column. `ExposureUpdate` now has field validators
   rejecting explicit `null` for `status`/`acknowledged` with a `422`
   *before* the handler runs at all — so a request mixing a bad field with
   an otherwise-valid change (e.g. `{"owner": "x", "status": null}`) touches
   nothing, not even `owner`, and writes no audit rows. `owner`,
   `remediation_target_date` and `remediation` remain nullable/clearable,
   matching their DB columns. `owner` also now has `max_length=200` to match
   `String(200)` — SQLite doesn't enforce VARCHAR length at all, so an
   oversized value would previously have passed silently on SQLite and only
   broken on PostgreSQL.
3. **The audit trail silently dropped some real changes.** Deacknowledging
   (`acknowledged: true → false`) wrote no history row at all (the code only
   logged the `true` direction); changing `remediation` wrote no history row
   at any point (no branch handled it). Both now audit every actual change
   in both directions with old/new values; a new `REMEDIATION_CHANGED` event
   type was added (migrated into the `exposure_history.event_type` `CHECK`
   constraint by the same migration as fix 1/4). No-op writes (new value
   equals current value) still produce no history row. `acknowledged` and
   `status` remain intentionally independent — see the comment in
   `exposures.py` — matching the spec's Exposure model, which lists both as
   separate fields rather than one deriving the other.
4. **Enums were documented but not actually enforced by the database.**
   `SAEnum(..., native_enum=False)` without `create_constraint=True` emits a
   plain, unconstrained `VARCHAR` — raw SQL (or any code bypassing the ORM's
   Python-level `Enum` type) could write any string. Fixed by adding
   `create_constraint=True` to the shared `sa_enum()` helper, plus `CHECK`
   constraints for every enum column added by the same migration as fix 1.
   Verified against a real migrated database (not just
   `Base.metadata.create_all`), with a raw SQL `UPDATE` attempting an
   invalid value.
5. **`/api/v1/health` reported an unusable database as healthy.** `SELECT 1`
   succeeds against any reachable SQLite file, migrated or not — it cannot
   tell "empty file" from "correctly migrated". The endpoint now checks the
   `alembic_version` table against the Alembic head revision on disk
   (`threnda.app.db.migration_status`) and returns `database: "ok"` /
   `"uninitialized"` / `"outdated"`, with **HTTP 503** for anything but
   `"ok"` — no raw driver/filesystem error text in the response, since this
   endpoint is unauthenticated. This is a readiness check, not a bare
   liveness probe; a separate liveness endpoint wasn't added since any HTTP
   response (including the 503) already answers "is the process up".
6. **The documented quickstart failed on a genuinely fresh clone.** The
   default `THRENDA_DATABASE_URL` (`sqlite:///./data/threnda.db`) needs
   `./data/` to already exist — SQLite will not create it, and `data/` is
   gitignored so it never exists in a fresh checkout. README now includes
   `mkdir -p data` before `alembic upgrade head`. Re-verified end to end in
   a genuinely clean copy of the tree with a brand-new virtualenv (fresh
   `pip install -e ".[dev]"`, migration, running server, full test suite).

Regression tests for all six live in `tests/test_independent_review.py`,
`tests/test_independent_relationship_review.py`,
`tests/test_independent_migration_review.py`, `tests/test_health.py`, and
`tests/test_db_session.py`. Full suite, `ruff check`, `ruff format --check`
and `mypy` all pass after the fixes; see the bugfix commit for exact counts.

## Milestone 2 — ingestion framework

**Status: done (implementation), pending owner review**

Scope: generic importer interface, CSV importer with a column-mapping
wizard, generic JSON importer, SARIF 2.1.0 importer, synthetic demo
datasets for each, `POST /api/v1/imports`, and (added during this
milestone — see "Design decisions") workspace/source provisioning
endpoints.

Independently reviewed against `docs/milestone-2-acceptance.md`; see that
checklist for the exact scenarios exercised.

Acceptance criteria:

- [x] `Importer` protocol with `detect()`/`parse()` — see "Design
      decisions" for why this is two stages, not four, and why that's a
      real simplification and not a coverage gap.
- [x] CSV importer: column mapping (title/asset/severity/cvss/cve/cwe
      /description/remediation/evidence), case-insensitive severity name
      normalization with a CVSS-derived fallback, mapping persisted on
      `Source.meta` and reused automatically on later uploads.
- [x] Generic JSON importer for a documented Threnda-native finding shape
      (`docs/importers.md`).
- [x] SARIF 2.1.0 importer, read against the public OASIS spec: `ruleId` /
      `ruleIndex` lookup across multiple runs, explicit vs. rule-default
      severity, physical locations, `helpUri` references, `kind`-aware
      (only `fail` results import), line-number-independent fingerprints,
      `partialFingerprints` preserved verbatim and folded into the
      fingerprint without inventing meaning for any individual key.
- [x] `POST /api/v1/imports`: real multipart upload, streaming request-size
      cap enforced at the ASGI layer (works without `Content-Length`),
      filenames never used for filesystem access, content validated
      against the source's declared format via `detect()`, workspace/source
      mismatch rejected before anything is read, explicit transaction
      semantics (a `FAILED` `ImportRun` survives a whole-file parse
      failure; a partially-invalid file persists every valid record and
      reports every invalid one, never silently discarding either).
- [x] `POST /api/v1/workspaces` and `POST /api/v1/sources` (plus
      `GET /api/v1/workspaces`) — not in the original API list, added
      because ingestion needs a documented, non-SQL way to create the
      workspace/source an upload belongs to.
- [x] Synthetic demo data: `demo-data/csv/synthetic-vulnerabilities.csv`
      (+ a follow-up export for Milestone 3's differential engine),
      `demo-data/json/synthetic-findings.json`,
      `demo-data/sarif/synthetic-scan.sarif.json`.
- [x] Tests: importer unit tests (CSV mapping edge cases, SARIF spec
      details, JSON normalization), upload API tests (happy path for all
      three formats through real HTTP, workspace isolation, malicious
      filenames, malformed content, out-of-range CVSS, mapping
      persistence/rejection), a standalone ASGI middleware test proving
      the size cap works without `Content-Length`, and an end-to-end
      "stop and restart the process" persistence test.
- [x] `ruff check`, `ruff format --check`, `mypy` clean for `threnda/`;
      124 tests pass.

### Design decisions made in this milestone

- **Two-stage `Importer` interface, not four.** The spec's
  `detect`/`validate`/`parse`/`normalize` collapses into `detect()` (cheap
  sniff) and `parse()` (validates, parses and normalizes in one pass,
  raising `ImportFormatError` for a whole-file problem and collecting
  per-record problems in `ParsedImport.errors`). Threading the same
  intermediate state through four separate method calls would not have
  changed what's validated — only added boundaries with no behavioural
  payoff, which is exactly the "unnecessary abstraction" CLAUDE.md asks to
  avoid.
- **CSV mapping storage.** Saved on `Source.meta["csv_mapping"]` rather
  than a new table. `Source` is already workspace-scoped, so this gets "a
  saved mapping belongs to its source/workspace" for free, and a JSON
  column that already exists costs nothing bigger than a dict update — a
  dedicated `SourceFieldMapping` table would be premature for the one
  format that needs a mapping at all.
- **Format is declared by `Source.source_type`, not sniffed per upload.**
  A source is a stable declaration of what it produces; `detect()` still
  runs against the actual bytes and rejects a mismatch (uploading a SARIF
  file to a source configured as CSV is a `422`, not a silent
  reinterpretation) — this is "validate the content-type claim", not
  "guess the format from content," which would make the same source behave
  differently upload to upload.
- **Provisional fingerprints.** `Finding.fingerprint` is `NOT NULL`, so
  every importer computes one now (`threnda/app/services/ingestion/fingerprint.py`)
  from whatever identifying fields it has (CVE/asset for CSV/JSON; rule +
  artifact URI + `partialFingerprints` for SARIF, deliberately excluding
  line numbers). This is explicitly provisional — Milestone 3's layered
  deduplication (deterministic → fuzzy → optional semantic) is a different,
  larger problem and may extend or replace this without a schema change.
- **Upload size limit is ASGI middleware, not a dependency check.** A
  dependency reading `Content-Length` can be bypassed by a client that
  omits the header (chunked transfer) or lies about it — by the time a
  FastAPI dependency runs, Starlette's multipart parser may have already
  buffered an unbounded body. `MaxBodySizeMiddleware` counts bytes as they
  stream through `receive()` and aborts mid-stream; `test_upload_middleware.py`
  proves this specifically for a request with no `Content-Length` header.
- **Failed-parse `ImportRun` is not rolled back.** The upload endpoint
  catches `ImportFormatError` itself and returns a `JSONResponse` directly
  instead of raising — the request handler completes normally, so the
  session commits the `FAILED` `ImportRun` it already wrote, instead of the
  generic per-request rollback-on-exception undoing it. Documented in
  `docs/importers.md` under "Transaction semantics".
- **Workspace/source provisioning endpoints, added mid-milestone.** The
  original API list (`docs/community-v0.1-spec.md`) has no `POST
  /api/v1/workspaces` or `POST /api/v1/sources` — but the Milestone 2
  acceptance checklist explicitly requires provisioning through "the
  documented local interface," not an undocumented SQL shell step, and
  there was genuinely no other way to create the workspace/source an
  upload needs. Minimal by design: name + type only, no bulk operations.

### Known limitations after Milestone 2

- No cross-import deduplication or differential engine yet — `Finding.fingerprint`
  exists but nothing correlates findings across imports into an `Exposure`
  (Milestone 3).
- Trivy/Nuclei/Semgrep/vendor importers are not implemented — the
  `Importer` interface is designed for them, but Community v0.1 ships only
  CSV/JSON/SARIF.
- Pentest report ingestion (structured JSON + PDF text extraction) is
  Milestone 6.
- No web UI — provisioning and uploads are HTTP-API-only for now.
- Still no authentication — `workspace_id` remains caller-supplied.

### Independent review fixes (post-Milestone-2, pre-Milestone-3)

Two more defects, reproduced independently against the Milestone 2 commit,
fixed in a separate local commit:

1. **The upload size limit returned 400, not the documented 413.**
   Reproduced with a real running server (`uvicorn` + `curl`, not just
   `TestClient`): with `THRENDA_MAX_UPLOAD_BYTES` set low, uploading a file
   over the limit through the real multipart `POST /api/v1/imports` route
   returned `400 There was an error parsing the body`. Root cause:
   `fastapi/routing.py` wraps `await request.form()` in
   `except Exception: raise HTTPException(400, ...)` — the middleware's
   bespoke `MaxBodySizeExceeded` exception, raised from inside `receive()`
   while FastAPI parses the multipart body, got caught by that handler and
   turned into a generic 400 before it ever reached the middleware's own
   `except` clause. FastAPI's code has one specific exemption from that
   broad handler: `except HTTPException: raise` — it passes an
   `HTTPException` through unchanged. Fixed by having
   `MaxBodySizeMiddleware` raise `starlette.exceptions.HTTPException(413,
   ...)` directly instead of a bespoke exception type; this is one of the
   exception types guaranteed to survive FastAPI's own body-parsing error
   handling and reach Starlette's built-in exception middleware, which
   converts it into the correct response. Verified three ways: a real
   `uvicorn`+`curl` reproduction (including a chunked-transfer request with
   no `Content-Length` header) confirming `413` and zero database writes;
   `tests/test_upload_size_limit_integration.py`, a genuine multipart
   integration test against the real app and the real
   `POST /api/v1/imports` route (not a minimal bare-`Request` ASGI app like
   `tests/test_upload_middleware.py`), covering the exact reported
   reproduction plus a hand-built multipart body with an understated
   `Content-Length` and one with none at all; and the existing generic
   ASGI-level tests, kept as-is since they're still valid lower-level
   coverage of the middleware itself.
2. **`mypy tests` was never actually checked.** `mypy threnda migrations`
   (the command this plan's own acceptance criteria cite) is clean, but
   nothing had ever run mypy over `tests/` — doing so found 24 errors
   across 4 files: missing generic type arguments on bare `dict`
   annotations, an untyped generator helper, `TestClient.data=` payloads
   passing `int` where the stub expects `str`/`bytes`, and
   `TestClient.app` (typed as a generic ASGI callable, not concretely
   `FastAPI`) not exposing `.state`. Fixed at the root cause in every case
   — explicit `dict[str, Any]`/`Iterator[bytes]` annotations, `str(...)`
   around integer form fields (which is what actually gets sent over the
   wire for a form field anyway), and threading a properly-typed `FastAPI`
   app fixture through instead of reading `.state` off a `TestClient` —
   not by adding `# type: ignore` comments or weakening any assertion.
   The documented check command (`README.md`) is now `mypy threnda tests`,
   and a plain `pip check` was added alongside it to catch a broken
   dependency set.

## Milestone 3 — deduplication and exposures

**Status: done (implementation), pending owner review**

Scope: layered deduplication (deterministic + fuzzy) with a manual-review
tier, exposure correlation, and the import differential engine
(`NEW`/`UNCHANGED`/`UPDATED`/`RESOLVED`/`REOPENED`). Full design writeup in
`docs/deduplication.md`.

Acceptance criteria:

- [x] Deterministic fingerprint generator using the available combination of
      CVE/CWE/asset identity/URL/rule ID/repo+file/image digest/normalized
      title, documented and stable across re-imports of unchanged data —
      this was already delivered in Milestone 2
      (`threnda/app/services/ingestion/fingerprint.py`) and needed no
      change; Milestone 3 builds correlation on top of it.
- [x] Fuzzy-matching layer for `POSSIBLE` confidence duplicates using a
      deterministic text-similarity algorithm, no new dependency
      (`difflib.SequenceMatcher`, stdlib) —
      `threnda/app/services/dedup/similarity.py`.
- [x] `EXACT`/`HIGH` confidence matches correlate automatically into an
      `Exposure`; `POSSIBLE` matches are surfaced for manual review (merge /
      keep separate) via `GET/POST /api/v1/possible-duplicates`, never
      auto-merged under any code path.
- [x] Import differential engine (`persist_parsed_import`,
      `threnda/app/services/ingestion/runner.py`) comparing a new
      `ImportRun` against the prior state of a `Source` and classifying
      each finding; per-run counts stored on the `ImportRun` itself.
- [x] Historical findings are never deleted; `RESOLVED` findings are marked
      (`resolved_at`) and can `REOPENED` (cleared) on reappearance without
      losing their original exposure link.
- [x] Tests: fingerprint stability (`test_dedup_similarity.py`), each
      confidence tier including the shared-signal gate
      (`test_dedup_correlation.py`), full differential classification
      (`test_differential_engine.py`), and the review-queue API including
      workspace scoping and the double-resolution guard
      (`test_api_possible_duplicates.py`).
- [x] `pytest` (158 tests), `ruff check`, `ruff format --check`,
      `mypy threnda tests` (strict), `pip check` all clean.

### Design decisions made in this milestone

- **Correlation runs exactly once per finding, only on a genuine `NEW`
  classification.** `UNCHANGED`/`UPDATED`/`REOPENED` findings already carry
  whatever `exposure_id` they were given the first time; re-running
  correlation on every re-import would risk silently moving an
  already-reviewed finding to a different exposure out from under a user.
  This also means a `POSSIBLE` candidate can never be resolved by the mere
  act of importing again — only the explicit review API can resolve it.
- **Candidate search is gated on a shared strong signal (CVE or asset), not
  a full-workspace title scan.** Comparing every new finding's title
  against every exposure would be both slow at scale and prone to matching
  titles that merely sound alike but share nothing else in common.
  `test_high_similarity_without_a_shared_signal_is_never_compared` proves
  the gate actually excludes an otherwise near-identical (0.978 similarity)
  title pair that shares no CVE or asset.
- **`Finding.resolved_at` is deliberately independent of `Exposure.status`.**
  The differential engine only ever sets/clears `resolved_at` on the
  `Finding` row; it never transitions the user-controlled `Exposure.status`
  workflow field. `CLAUDE.md` lists "automated ownership" as an
  Enterprise-only capability — inferring an exposure's workflow state from
  source data alone would cross that boundary. A human still decides when
  an exposure itself is closed.
- **No bulk-merge or score-threshold auto-merge endpoint for `POSSIBLE`
  candidates.** Every resolution is one explicit, individually-recorded
  `POST .../merge` or `POST .../keep-separate` call. This was an explicit,
  non-negotiable constraint for this milestone, not a design preference.
- **`uq_findings_workspace_source_fingerprint` needed a pre-flight migration
  check.** Milestone 2's importer never checked for existing fingerprints
  before insert, so an existing database could already violate the new
  constraint. Migration `32832b96f2c3` runs a read-only
  `_assert_no_duplicate_fingerprints()` check before any DDL and raises
  cleanly (zero schema/data changes) if it finds any — the same pattern
  used for the Milestone 1 constraint-safety fix.
- **`PossibleDuplicate` uses the same composite-FK-scoped-by-`workspace_id`
  pattern as every other cross-table reference in this schema**
  (`ForeignKeyConstraint` on `(workspace_id, finding_id)` and
  `(workspace_id, candidate_exposure_id)`), rather than a bare integer FK,
  so a `PossibleDuplicate` cannot reference a finding or exposure from a
  different workspace even via raw SQL.

### Known limitations after Milestone 3

- Layer 3 (semantic/embedding-based matching) is not implemented — the
  spec gates it on local AI, which Community does not enable by default
  (Milestone 6 territory).
- The differential engine only compares a finding against other findings
  from the *same* `Source`; cross-source correlation happens only through
  the correlation engine's own Layer 1/2 signals, not through the
  differential classification itself. This matches the spec's model:
  differentials are a per-source concept, correlation is a per-workspace
  one.
- No web UI yet for the review queue — `/api/v1/possible-duplicates` is
  HTTP-API-only, same as the rest of Community so far (Milestone 5).
- Still no authentication — `actor`/`resolved_by` remain caller-supplied
  strings, same caveat as `ExposureHistory.actor` since Milestone 1.

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
