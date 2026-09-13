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
      (`test_differential_engine.py`), the review-queue API including
      workspace scoping and the double-resolution guard
      (`test_api_possible_duplicates.py`), CSV reorder/content-edit identity
      stability (`test_csv_reorder_stability.py`), and the legacy-database
      backfill migration including its idempotency guarantee
      (`test_migration_backfill.py`).
- [x] `pytest` (172 tests), `ruff check`, `ruff format --check`,
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
- **`uq_findings_workspace_source_finding_id` needed a pre-flight migration
  check.** Milestone 2's importer never checked for existing identities
  before insert, so an existing database could already violate the new
  constraint. Migration `32832b96f2c3` runs a read-only
  `_assert_no_duplicate_source_finding_ids()` check before any DDL and
  raises cleanly (zero schema/data changes) if it finds any — the same
  pattern used for the Milestone 1 constraint-safety fix. (Originally named
  and keyed on `fingerprint`; renamed as part of the independent-review
  fixes below — see there for why.)
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

### Independent review fixes (post-Milestone-3-first-cut)

An independent review (`docs/milestone-3-review.md`), run against a real
Milestone-2 database upgraded to Milestone 3 and real HTTP uploads (not
just unit tests), found five defects. All five are fixed in a follow-up
local commit; full design rationale for each is in
`docs/deduplication.md`, "Independent review fixes":

1. **Legacy findings never entered the queue.** Upgrading a real
   Milestone-2 database with unlinked findings created no `Exposure` or
   review candidate for any of them — `GET /exposures` couldn't see them
   and neither could the review queue. Fixed by having migration
   `32832b96f2c3` run every pre-existing unlinked finding through the same
   `correlate_new_finding` a live import's new finding uses (deterministic
   EXACT/HIGH auto-correlate, POSSIBLE creates a review candidate and is
   never auto-merged, no signal gets a standalone exposure) — idempotent,
   and never overwriting a finding that already has a link.
2. **A stable source-local id didn't survive a title edit.** Two JSON
   imports using the same explicit `id` but a changed title reported
   `NEW=1, RESOLVED=1` instead of `UPDATED=1`, because the differential
   engine matched on `fingerprint` — which importers legitimately bake
   title into. Root-caused as two identities being conflated into one
   field. Fixed by introducing a real split: `Finding.source_finding_id`
   (source-local identity; what the differential engine matches on) versus
   `Finding.fingerprint` (cross-source correlation signal; allowed to
   change with content). The unique constraint moved from
   `(workspace_id, source_id, fingerprint)` to
   `(workspace_id, source_id, source_finding_id)`, named
   `uq_findings_workspace_source_finding_id`. CSV's positional
   `row-{index}` identity fallback was also replaced with a content-derived
   one (same fields as `fingerprint`), since switching the differential
   engine to match on `source_finding_id` would otherwise have made
   reordering a re-exported CSV's rows look like a mass resolve+new — a
   regression the old, accidentally-reorder-safe fingerprint-based matching
   didn't have. Both fixes and the CSV trade-off are covered by
   `tests/test_independent_milestone3_review.py` (reviewer-authored) and
   `tests/test_csv_reorder_stability.py`.
3. **A partially-erroring import could falsely resolve an untouched
   finding.** A row that fails to normalize never reaches
   `parsed.findings`, so its identity can't be told apart from "genuinely
   absent" — treating every unseen active identity as RESOLVED whenever an
   import had any row errors risked falsely resolving a finding whose only
   problem was a malformed row that time. Fixed by suppressing the
   absence-based RESOLVED step entirely for any run with `parsed.errors`
   non-empty; successfully-parsed records in that same run are still fully
   processed.
4. **Duplicate identities inside one upload returned a raw HTTP 500.** Two
   records sharing a `source_finding_id` in one file both tried to become
   the row for that identity, and the second insert hit the unique
   constraint as an unhandled `IntegrityError`. Fixed by checking for
   intra-batch collisions (`find_duplicate_source_finding_ids`) before
   `persist_parsed_import` writes anything at all, raising
   `DuplicateIdentityError`; the upload API handles it exactly like a
   whole-file `ImportFormatError` — a durable `FAILED` `ImportRun` and a
   `422`, never a partial write.
5. **Aggregate counters couldn't say which finding changed.**
   `ImportRun.new_findings`/etc. answer "how many", not "which ones", and
   `Finding.import_run_id`/`resolved_at` are overwritten on every
   re-import. Fixed by auditing every RESOLVED/REOPENED transition against
   the finding's linked exposure as an `ExposureHistory` entry
   (`new_value="finding_id={id}"`), reusing the event types the model
   already defined for this. A finding not yet linked to an exposure (still
   pending POSSIBLE review) has nothing to audit against and is simply
   skipped, not an error.

Verification: full `pytest` (172 tests, up from 158), `ruff check`,
`ruff format --check`, `mypy threnda tests` (strict), `pip check` all
clean; plus a manual end-to-end pass against a real `uvicorn` server
covering all five scenarios (a real Milestone-2-schema database migrated to
head with a seeded unlinked finding appearing in `GET /exposures`; a real
title-change JSON re-upload reporting `UPDATED`; a real duplicate-identity
JSON upload returning `422` with a durable `FAILED` `ImportRun`, not a 500;
and a real partially-erroring JSON re-upload reporting `resolved_findings=0`).

## Milestone 4 — workflow, history, risk scoring

**Status: done (implementation), pending owner review**

Scope: full exposure lifecycle enforcement, explainable rule-based risk
scoring, richer API.

Acceptance criteria:

- [x] Lifecycle transitions enforced server-side (`OPEN` → `ACKNOWLEDGED` →
      `IN_PROGRESS` → `RESOLVED`/`RISK_ACCEPTED`, with `REOPENED` handling)
      — enforced in both the JSON API (`409` on an invalid move) and the
      web UI (the status `<select>` only ever offers a currently-valid
      next status, in addition to also rejecting server-side).
- [x] Rule-based risk score (0-100) from severity, CVSS, asset environment,
      internet exposure (if known), exploit evidence (if known), finding
      age, and manual business criticality — each contributing an
      explainable, labelled delta. Deterministic, no AI.
- [x] `GET /api/v1/exposures/{id}` includes the score breakdown, and the
      web detail panel's Risk tab renders it plus lets a user set the two
      manual asset inputs.
- [x] Rules configurable in code (`threnda.app.services.risk_scoring.RiskScoringConfig`,
      with explicit construction-time validation) and structured so a
      later user-configuration UI can build the same validated object from
      stored values without changing the scoring engine itself.
- [x] Tests: every scoring input in isolation plus clamping boundaries
      (`tests/test_service_risk_scoring.py`), every allowed/forbidden
      lifecycle edge (`tests/test_service_exposures.py`), API/UI
      integration (`tests/test_api_exposures.py`, `tests/test_api_assets.py`,
      `tests/test_web_risk.py`), the new migration
      (`tests/test_migration_risk_fields.py`), and the ingestion/recompute
      wiring (`tests/test_differential_engine.py`).

### Design decisions made in this milestone

- **Lifecycle graph is a DAG, not the acceptance criteria's literal single
  chain.** The written chain (`OPEN → ACKNOWLEDGED → IN_PROGRESS →
  RESOLVED/RISK_ACCEPTED`) is the *canonical* path, but forbidding every
  shortcut (e.g. `OPEN → RISK_ACCEPTED` directly) would have regressed
  Milestone 5's already-shipped bulk actions ("Mark in progress"/"Accept
  risk," both applied to plain `OPEN` exposures) and forced pointless
  clicks for real triage. What the graph does forbid: moving *backward*
  between active states without an explicit reopen, and treating a closed
  state (`RESOLVED`/`RISK_ACCEPTED`) as anything but a dead end you
  explicitly reopen — the only edge out of either is back to `OPEN`. See
  `threnda/app/services/exposures.py`'s `ALLOWED_STATUS_TRANSITIONS`.
- **Two new Asset fields, one new Finding field — all additive, all
  nullable/defaulted, none scanner-specific.** `Asset.internet_facing`
  (nullable bool) and `Asset.business_criticality` (new `AssetCriticality`
  enum, default `unknown`) are manual-only: no public CSV/JSON/SARIF
  format carries either concept, and the spec calls criticality
  explicitly manual. `Finding.exploit_evidence` (nullable bool) *can* come
  from data — Threnda's own generic JSON format gained one new optional
  key for it — but CSV/SARIF are untouched, since neither public format
  has a field for it.
- **`deferred=True` on all three new columns.** Migration `32832b96f2c3`'s
  legacy-finding backfill (and the live correlation code it reuses)
  selects whole `Finding`/`Asset` entities from a point in the upgrade
  sequence *before* this milestone's migration has added these columns to
  the table — an undeferred column would put its name in that implicit
  SELECT's column list and break a from-scratch `alembic upgrade head`.
  Deferring keeps a newly-added column out of the default load; risk
  scoring explicitly accesses (and transparently lazy-loads) it wherever
  it actually needs it, which is always after this migration has run.
- **Risk-score recompute is wired into specific mutation points, not
  lazily computed for the list view.** `Exposure.risk_score` (used for
  list sorting/filtering) is recomputed and persisted at exposure
  creation, a finding being linked (including via the possible-duplicate
  review actions), a linked finding's content changing on re-import, and
  a linked asset's manual fields changing. `GET /api/v1/exposures/{id}`
  instead computes fresh on every call, so the single-item view (and its
  breakdown) is always live even in the one gap the persisted value has:
  pure time passing an age-bucket boundary with no other mutation since —
  Community has no background worker, so this is a documented limitation,
  not a silent inaccuracy.
- **Recompute calls live in the ingestion/possible-duplicates orchestration
  layer, not inside `threnda.app.services.dedup.correlation`.** The first
  cut called `recompute_and_persist_risk_score` from inside
  `create_standalone_exposure`/`link_finding_to_exposure` themselves, which
  broke the same from-scratch migration for the same reason as the
  deferred-column issue above (those two functions are exactly what
  migration `32832b96f2c3`'s backfill calls). Moved the calls to their
  callers (`threnda.app.services.ingestion.runner`,
  `threnda.app.services.possible_duplicates`) instead, which are never
  invoked from a historical migration — keeping `correlation.py` itself
  free of any dependency on schema a later migration adds.
- **No audit trail for `Asset` changes.** `ExposureHistory` is scoped to
  the exposure workflow per the spec's own data model; adding an
  equivalent table for two manually-set asset fields wasn't asked for and
  would be a bigger addition than the fields themselves.

### Known limitations after Milestone 4

- The persisted list-view risk score does not update purely from elapsed
  time (the age-bucket component) without some other mutation — see
  "Design decisions" above. `docs/risk-scoring.md` documents this.
- No standalone Asset management page in the web UI — the Risk tab can
  edit an asset's two manual fields only in the context of an exposure
  that links to it.
- `Asset` has no audit trail — a `business_criticality`/`internet_facing`
  change is not individually recorded anywhere (unlike every `Exposure`
  field change).

## Milestone 5 — web UI

**Status: done (implementation), pending owner review**

Scope: Jinja2 + HTMX UI — findings list, exposure detail, imports, sources,
review queue, minimal dashboard.

Acceptance criteria:

- [x] Findings/exposures list: search (title), status/severity filters,
      sorting (risk score/age/last seen), pagination.
- [x] Exposure detail: full field set from the spec, action buttons
      (acknowledge, assign owner, set target date, change status, add note,
      a link into the review queue when a pending possible-duplicate
      candidate points at the exposure).
- [x] Imports list and a new-import wizard with visible error reporting
      (differential counts and row errors from the real upload response).
- [x] Sources list and creation.
- [x] Minimal dashboard (open/critical/high/overdue/new this week/resolved
      this week/average age of open exposures) — deliberately not a
      visualization showpiece.
- [x] CSRF protection on every state-changing form (double-submit cookie);
      Jinja2's default autoescaping verified (no `|safe`/`Markup` used
      anywhere in the templates).
- [x] Tests: route rendering, filter/search/sort behaviour, CSRF
      enforcement (missing and wrong token), workspace isolation, bulk
      actions, the CSV mapping wizard, review-queue merge/keep-separate —
      `tests/test_web_*.py`.

### Design decisions made in this milestone

- **No separate "preview" step for uploads.** `POST /api/v1/imports` parses,
  validates and persists in one transaction — there is no dry-run endpoint,
  so the wizard is honestly two steps (pick source + file, see the result),
  not the three-step preview flow a design mockup might suggest.
- **CSV column mapping via a small htmx round trip, not client-side
  parsing.** `POST /imports/sniff` reads just the file's header row
  server-side (the same decode path `CsvImporter.parse` uses) and pre-fills
  the mapping table with the existing `guess_mapping` heuristic
  (`threnda/app/services/ingestion/csv_importer.py`) — no CSV parsing
  logic is duplicated in JavaScript.
- **Sorting/filtering the queue is a web-layer-only query, not a change to
  `GET /api/v1/exposures`.** Arbitrary column sorting isn't part of that
  endpoint's documented contract (it always orders by risk score); the web
  route builds its own `select(Exposure)` with the same filter predicates
  plus a `sort` parameter, rather than extending the versioned JSON API's
  behaviour for a UI-only need.
- **Three extractions into `threnda/app/services/`** —
  `exposures.apply_exposure_update`, `possible_duplicates.resolve_merge`/
  `resolve_keep_separate`, and `ingestion/upload.handle_upload` — pulled out
  of their API route handlers so the web UI's write actions run through
  exactly the same audited/correlated/differential logic as the JSON API,
  never a second copy of it. Every existing HTTP-level test for those
  routes still passes unchanged; see `tests/test_service_*.py` for the new
  unit-level coverage of the extracted functions themselves.
- **CSRF without a session.** No authentication exists yet (Milestone 7),
  so there is nothing to bind a session-tied CSRF token to. Uses the
  double-submit-cookie pattern instead (`threnda/app/web/csrf.py`): a
  random token cookie, echoed into every form as a hidden field and
  compared back on every POST. Documented as a placeholder for Milestone
  7's real session/CSRF story, same framing as the `actor` placeholder
  used throughout the API.
- **Workspace resolution without auth.** `threnda.app.web.deps.get_current_workspace`
  resolves `?workspace_id=` (validated) > a `threnda_workspace_id` cookie
  (validated) > the first workspace by id, raising a redirect to
  `/workspaces/new` if none exist. The cookie is a browsing convenience,
  not an access boundary — identical caveat to the API's caller-supplied
  `workspace_id`.
- **No settings page.** A prototype design reference included one showing
  local accounts, API tokens and SSO — none of which exist before
  Milestone 7. CLAUDE.md rules out describing unimplemented behaviour as
  working, so it was left out rather than built with placeholder data.
- **No command palette / row-cursor keyboard navigation.** A design
  reference included a `⌘K` command palette and `j`/`k` row navigation;
  both would need a meaningful amount of hand-rolled JavaScript for a
  "minimal JavaScript" server-rendered app (CLAUDE.md). `static/js/app.js`
  is three small enhancements instead: `/` focuses search, `Esc` closes the
  detail panel, and the bulk-action bar shows a live selection count.
- **Fonts and htmx are vendored/system, not CDN-loaded.** A design
  reference used hosted Google Fonts; Community is self-hosted and
  offline-first, so the UI uses system font stacks instead. htmx itself is
  vendored at `static/vendor/htmx.min.js` rather than loaded from a CDN,
  for the same reason.

### Known limitations after Milestone 5

- No "risk range" filter control in the queue UI — `Exposure.risk_score` is
  sortable/filterable at the data layer but always `0` until Milestone 4's
  scoring engine exists, so a dedicated range slider wasn't built yet.
- The queue's detail panel does not live-refresh the corresponding table
  row after an edit (e.g. an updated status badge) without reloading the
  queue — a nice-to-have, not a correctness gap (the underlying data is
  always updated correctly).
- Import detail pages cannot show historical per-row error messages —
  `ImportRun` only stores aggregate counts; the detailed messages are
  returned once, in the upload response itself (see docs/importers.md).
- Still no authentication — the workspace cookie and CSRF token are both
  browsing conveniences, not access control, same caveat as every `actor`
  field in this codebase since Milestone 1.

## Milestone 6 — pentest ingestion, optional local AI

**Status: done (implementation), pending owner review**

Scope: structured pentest JSON import, deterministic local PDF/text
extraction and candidate-finding framework, `AIProvider` abstraction with
`DisabledAIProvider` and `OllamaAIProvider`.

Acceptance criteria:

- [x] Structured pentest JSON importer
      (`threnda/app/services/ingestion/pentest_importer.py`) — its own
      top-level shape (`report` + `affected_assets`), one
      `NormalizedFinding` per affected asset (fan-out), idempotent through
      the existing Milestone 3 differential engine. See
      `docs/importers.md`, "Pentest report JSON".
- [x] Local, deterministic PDF/text extraction
      (`threnda/app/services/documents/extraction.py`) with section
      detection (`threnda/app/services/documents/section_detection.py`) and
      a candidate-finding review framework
      (`threnda/app/models/document.py`, `threnda/app/models/candidate_finding.py`,
      `threnda/app/services/documents/processing.py`,
      `threnda/app/services/documents/review.py`); no external OCR or AI
      API calls; manual accept/reject of candidates works identically with
      AI disabled. See `docs/pentest-and-ai.md`.
- [x] `AIProvider` interface: `summarize_finding`, `explain_finding`,
      `suggest_remediation`, `extract_findings_from_text`,
      `compare_findings` (`threnda/app/services/ai/provider.py`).
- [x] `DisabledAIProvider` (the Milestone 6 default, verified zero-I/O) and
      `OllamaAIProvider` (`THRENDA_AI_PROVIDER=ollama`,
      `THRENDA_AI_MODEL=<model>`, `THRENDA_AI_BASE_URL` restricted to
      loopback), calling a local Ollama instance only — no cloud LLM calls
      anywhere in the application. Threnda continues functioning normally
      (heuristic candidates unaffected) if Ollama is unreachable or
      misconfigured. See `docs/local-ai.md`.
- [x] AI output is visibly marked as machine-generated
      (`CandidateFinding.ai_assisted`/`ai_model`, the "AI GENERATED" badge
      in the web UI) and never silently modifies a security record — every
      AI-derived candidate still requires the same explicit human accept
      action as a heuristic one.
- [x] Tests: pentest JSON import (fan-out, idempotency, per-record errors,
      duplicate identity), PDF/TXT extraction (malformed, truncated,
      encrypted with both an empty and a real user password, image-only,
      oversized), prompt-injection-style text treated as inert data end to
      end, `DisabledAIProvider` zero-I/O behaviour, `OllamaAIProvider`
      against a fully mocked HTTP layer (no real Ollama dependency
      anywhere), document-hash idempotency, manual accept/reject with
      immutable decisions and cross-workspace denial, and a dedicated
      regression test that accepting one candidate never resolves an
      unrelated finding on the same source — see
      `tests/test_importer_pentest.py`, `tests/test_documents_extraction.py`,
      `tests/test_documents_section_detection.py`,
      `tests/test_documents_processing.py`,
      `tests/test_service_documents_review.py`, `tests/test_ai_provider.py`,
      `tests/test_api_documents.py`, `tests/test_web_documents.py`.

Milestone 8 later supersedes Ollama as the current Community default with a
Threnda-managed llama.cpp/Qwen bundle while preserving both Milestone 6
providers as explicit compatibility options.

### Design decisions made in this milestone

- **The AI provider is only ever constructed when `use_ai=True`.** The
  initial draft called `build_ai_provider(settings)` unconditionally on
  every document upload; a misconfigured `THRENDA_AI_MODEL` with
  `THRENDA_AI_PROVIDER=ollama` would then break *every* upload, including
  ones that never asked for AI. Fixed by deferring construction into
  `process_document`'s `use_ai` branch
  (`threnda/app/services/documents/processing.py::_resolve_ai_provider`),
  and by having `build_ai_provider` itself wrap a construction-time
  `ValueError` as `AIUnavailableError` — the same exception type every
  other "no AI this time" condition already uses.
- **`accept_candidate` calls `persist_parsed_import(..., infer_resolved=False)`.**
  The default differential-engine behaviour infers `RESOLVED` for every
  active finding on a source that a run didn't mention — correct for a
  real re-upload, but accepting a single candidate is not a re-upload of
  the whole source; it would otherwise silently resolve every other active
  finding already on that source.
- **`Document`/`CandidateFinding` follow the exact same workspace-scoped
  composite-FK pattern as every Milestone 1–4 table** (see
  `threnda/app/models/document.py`, `candidate_finding.py`) — no new
  cross-cutting pattern was introduced for this milestone.
- **The web sidebar's "Documents" nav count shows pending candidates, not
  total documents** (mirroring "Review"'s pending-possible-duplicate
  count) — the actionable number, not an inventory count; total document
  count is still shown on the `/documents` page itself.

## Milestone 7 — authentication, hardening, packaging, docs

**Status: done (implementation), pending owner review**

Scope: local accounts, session-derived authorization replacing every
caller-supplied `workspace_id`, CSRF/security-header hardening, an
administrative audit log, CSV/JSON export, a generic SSRF-defended
outbound webhook, Docker packaging, and the full documentation set.

Acceptance criteria:

- [x] Local username/password accounts (`threnda/app/models/user.py`),
      Argon2id password hashing via `argon2-cffi`
      (`threnda/app/services/auth.py`), secure session cookies
      (`threnda_session`: `HttpOnly`, `SameSite=Strict`, `Secure` in
      production/HTTPS) with opaque random tokens stored only as a
      SHA-256 hash, idle (2h) and absolute (12h) expiry, rotation on
      login, and explicit revocation on logout. Every workspace-scoped
      API/web route now depends on `require_viewer`/`require_analyst`/
      `require_admin` (`threnda/app/api/deps.py`,
      `threnda/app/web/deps.py`) — a caller-supplied `workspace_id` is no
      longer itself the authority; it only grants access once matched
      against a `WorkspaceMembership` row for the authenticated session's
      user. A workspace that doesn't exist and one the caller isn't a
      member of both answer `404`, never `403`, so neither leaks whether
      the other exists.
- [x] First-admin bootstrap is explicit and one-time: `threnda
      bootstrap-admin` (`threnda/app/cli.py`) refuses to run once any
      account exists, refuses to run against an unmigrated database, reads
      credentials from `THRENDA_BOOTSTRAP_ADMIN_USERNAME`/`_PASSWORD` (for
      the Docker entrypoint) or prompts interactively via `getpass`
      otherwise, and never invents a credential. `threnda/app/core/
      startup_checks.py::validate_production_readiness` fails the whole
      application closed at startup if `THRENDA_ENV=production` and
      `THRENDA_SECRET_KEY` is missing, the placeholder `change-me`, under
      32 characters, or if `THRENDA_ALLOW_INSECURE_COOKIES` is set.
- [x] Workspace membership and roles: admin/analyst/viewer
      (`threnda/app/models/enums.py::WorkspaceRole`), hierarchical
      (`threnda.app.services.memberships.role_at_least`). Admin manages
      members (`Settings → Members`, `/api/v1/members`) and webhooks;
      analyst additionally operates findings/imports/documents/possible-
      duplicates/exports; viewer is read-only. A workspace can never be
      left with zero admins (`LastAdminError`). No superuser/global-admin
      concept — an admin's authority never crosses a workspace boundary
      they don't belong to.
- [x] CSRF tokens cryptographically bound to the session
      (`HMAC-SHA256(THRENDA_SECRET_KEY, "csrf:" + session_token)`,
      `threnda/app/web/csrf.py`) on every state-changing web form; a
      pre-session anonymous double-submit cookie covers the login form
      itself. Baseline response security headers on every response
      (`threnda/app/core/middleware.py::SecurityHeadersMiddleware`):
      `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
      `Cross-Origin-Opener-Policy`, `Permissions-Policy`, and a
      `Content-Security-Policy` with `script-src 'self'` (the UI has no
      inline script/event-handler anywhere — the one inline `onclick=`
      handler this milestone found was moved to a delegated listener in
      `static/js/app.js` to keep that true). Upload caps, safe filenames,
      content validation, no shell execution, no unsafe deserialization —
      all unchanged from Milestones 2/6, still in effect.
- [x] Administrative/security-sensitive changes audited
      (`threnda/app/models/audit_log.py`, single write path
      `threnda/app/services/audit.py`): account creation, login
      success/failure, logout, membership add/role-change/remove, webhook
      create/update/delete/delivery, export downloads. Routine
      finding/exposure edits keep using the pre-existing `ExposureHistory`
      trail rather than being duplicated here. `record_audit_event`
      defensively rejects any `detail` key that looks like it could hold a
      secret (`password`/`secret`/`token`/`session`/`authorization`/
      `cookie`/`hash`), so raw findings, passwords, session tokens, and
      webhook secrets are never logged anywhere, audit log included.
- [x] `GET /api/v1/exports/exposures?format=csv|json`
      (`threnda/app/services/exports.py`), analyst role or higher: a
      stable, documented column schema (docs/exports.md), CSV
      formula-injection safety (OWASP mitigation — a leading `'` on any
      field starting with `=+-@`/tab/CR), keyset-paginated streaming (no
      cap on row count, bounded memory per request), and an audit log
      entry per download.
- [x] Generic outbound webhook (`threnda/app/services/webhooks.py`,
      `threnda/app/models/webhook_endpoint.py`): disabled by default,
      created/enabled/triggered only by an admin, one synchronous signed
      HTTP POST per explicit trigger (no automatic per-event delivery, no
      queue — Community has no background worker). The signing secret is
      encrypted at rest (Fernet, keyed from `THRENDA_SECRET_KEY`) and shown
      to the admin exactly once, at creation. SSRF defense: HTTPS required
      in production, resolved-address checking against
      private/loopback/link-local/reserved/multicast/unspecified ranges
      (covers the cloud metadata address as an ordinary link-local address)
      both at save time and immediately before every delivery, redirects
      never followed, strict timeout and a bounded response-read size.
      `THRENDA_WEBHOOK_ALLOW_PRIVATE_TARGETS` is a documented
      local-development/test-only override. See docs/webhooks.md,
      docs/security.md ("Webhooks — SSRF defense") for the one documented
      residual limitation (a narrow DNS-rebinding race between the
      pre-delivery check and the connection a moment later).
- [x] Docker packaging: a multi-stage, non-root `Dockerfile` (a builder
      stage with a compiler for any wheel-less platform, a slim runtime
      stage that drops root before the process runs) and a
      `docker-compose.yml` running PostgreSQL, with healthchecks on both
      services, automatic `alembic upgrade head` (and optional
      bootstrap-admin) via `docker/entrypoint.sh` before the app ever
      serves traffic, the app bound to `127.0.0.1:8080` on the host, named
      volumes for both Postgres data and the app's `/app/data`, and no
      hardcoded secret anywhere — `THRENDA_SECRET_KEY`/`POSTGRES_PASSWORD`
      use Compose's `${VAR:?message}` interpolation, so `docker compose up`
      refuses outright with a clear message if either is unset. Verified
      with a real `docker compose up -d --build` from a clean `.env`
      against the actual Dockerfile/compose file in this repository — see
      "Verification" below.
- [x] Complete documentation: `docs/architecture.md`, `docs/data-model.md`,
      `docs/security.md` (accounts/roles/passwords/sessions/CSRF/response
      headers/authorization/auditing/webhook SSRF/secret rotation/
      fail-closed checks), `docs/roadmap.md`, `docs/deployment.md`
      (bootstrap, TLS/reverse proxy, backup/restore, upgrades, a release
      checklist), `docs/webhooks.md`, `docs/exports.md`; `docs/importers.md`
      and `docs/pentest-and-ai.md` updated for the now-authenticated
      contract (the `actor` field client-supplied on document
      accept/reject is gone — it's always the session's username); README
      quick start rewritten for both the Docker Compose path and local
      Python development, including a real authenticated `curl` walkthrough
      (session cookie via `-c`/`-b`) for provisioning, uploading, and
      exporting. `.env.example` documents every new setting.
- [x] Full local test suite green; Definition of Done from
      `docs/community-v0.1-spec.md` walked through end to end. See
      "Verification" below for exact commands and results.

### Design decisions made in this milestone

- **`workspace_id` stays a request parameter, but stops being an
  authority.** The spec's original API (Milestones 1–6) always took
  `workspace_id` as a caller-supplied query parameter, and a user can
  legitimately belong to more than one workspace — removing the parameter
  entirely wasn't the right fix. Instead, every route now resolves it
  through a membership-checking dependency
  (`get_workspace_membership`/`get_current_membership`) that answers `404`
  for both "no such workspace" and "not a member of that workspace" before
  the route handler ever runs, and the handler always reads the
  *authorized* workspace id off the resolved `WorkspaceMembership`, never
  trusting the raw parameter again.
- **CSRF without a stored token.** Rather than adding a database table or
  server-side cache for CSRF tokens, the token is
  `HMAC(secret_key, "csrf:" + session_token)`, recomputed identically on
  generation and verification. This is a synchronizer token cryptographically
  bound to the session (stronger than a bare double-submit cookie) with zero
  additional storage and automatic rotation whenever the session itself
  rotates (login). The one place this needed care: `build_shell_context`
  (`threnda/app/web/deps.py`) now generates the token once per request and
  puts it in the shared shell context, because the sidebar's own logout
  form needs one on *every* full-page route, not just the ones that
  already happened to compute one for their own form.
- **`actor` is no longer client-supplied anywhere.** Every write endpoint
  that used to accept an `actor` query parameter or JSON field (a
  Milestone-1-era placeholder, documented at the time as "until
  authentication exists") now derives it from `membership.user.username`
  or `session.user.username` instead. This changes several endpoints'
  request contracts intentionally — most visibly, `POST
  /api/v1/documents/candidates/{id}/reject` no longer takes a request body
  at all (its only field was `actor`). Existing tests and docs were updated
  to the new, authenticated contract rather than kept passing an
  now-ignored field.
- **A real PostgreSQL bug this milestone's Docker verification found, in a
  Milestone 3 migration.** `alembic upgrade head` against a fresh
  PostgreSQL database (via `docker compose up`) failed with
  `InvalidForeignKey` inside migration `32832b96f2c3`: it created the
  `possible_duplicates` table (whose foreign key targets
  `findings(workspace_id, id)`) *before* adding the
  `uq_findings_workspace_id_id` constraint those columns need, later in
  the same migration. SQLite's batch-mode table rebuilding doesn't validate
  this at `CREATE TABLE` time, so every prior milestone's SQLite-only test
  suite (172+ tests, run before this milestone) never caught it — this is
  exactly the kind of bug CLAUDE.md's "PostgreSQL-compatible architecture"
  requirement exists to catch, and it only surfaced once something in this
  milestone actually ran a migration against real PostgreSQL. Fixed by
  reordering the migration's own `upgrade()` (findings' constraints added
  before `possible_duplicates` is created) and `downgrade()` (mirrored:
  `possible_duplicates` dropped before the constraints it depends on) —
  verified with a real `docker compose up` against Postgres, and the
  existing SQLite upgrade/downgrade/upgrade round trip still passes
  unchanged.
- **No superuser / global admin.** Considered and rejected: a superuser
  role would be a single point of compromise across every workspace and
  contradicts the workspace-scoped authorization model this milestone
  otherwise establishes. The operator's remedy for a workspace nobody can
  administer any more (e.g. every admin account was deleted at the
  database level, which the application itself never does) is direct
  database access — the same as any other self-hosted single-tenant tool,
  and no worse than before this milestone (there was no admin concept to
  lock anyone out of previously).
- **Webhook secret is encrypted, not hashed.** Every other secret in this
  schema (passwords, session tokens) is stored as a one-way hash because
  the application only ever needs to *verify* a value a client presents
  again. A webhook signing secret is the opposite: Threnda itself must
  recover the plaintext to HMAC-sign each outgoing delivery, so it cannot
  be a one-way hash. Encrypted at rest instead (Fernet, keyed from
  `THRENDA_SECRET_KEY`) — the one deliberate exception to "never store a
  recoverable secret" in this codebase, documented as such in
  `threnda/app/services/crypto.py` and docs/security.md.
- **Rate limiting is a real, but bounded, control.** The per-IP login
  limiter is intentionally a simple in-process sliding window, not a
  Redis-backed distributed limiter — CLAUDE.md rules out Redis without
  strong justification, and Community's shipped `docker-compose.yml` runs
  one Uvicorn worker. This is documented as a real limitation (does not
  survive a restart, not shared across multiple worker processes) rather
  than silently assumed to be enterprise-grade; the durable, per-account
  lockout is the control that actually matters against a sustained attack
  on one known username.

### Known limitations after Milestone 7

- The per-IP login rate limiter is process-local (see "Design decisions"
  above) — running more than one Uvicorn worker reduces its effectiveness
  proportionally. The per-account lockout is unaffected.
- The webhook feature has a documented, narrow DNS-rebinding race between
  its pre-delivery SSRF check and the actual connection a moment later
  (docs/security.md, "Webhooks — SSRF defense"). Closing it fully would
  need a custom resolver pinning the connection to the exact address that
  was checked, which this milestone's scope (a rarely-used, admin-only,
  manually-triggered generic webhook with no automatic delivery) does not
  justify.
- No self-registration and no password-reset/forgot-password flow — an
  account locked out of every workspace it belongs to, or a user who
  forgot their password, needs another admin (or, for the very first
  admin, direct database access) to help; there is no email-based recovery
  anywhere, consistent with Community having no required outbound mail
  dependency.
- No multi-factor authentication.
- The web UI does not yet hide write-action buttons/forms from a `viewer`
  role in every template (server-side enforcement via `require_analyst` is
  complete and is what actually matters for security; a viewer who submits
  a hidden form still gets a `403`, just without the button being
  pre-emptively hidden everywhere it could appear).
- Exports are exposure-level only (no findings/assets/audit-log export
  endpoint) — see docs/exports.md.

### Verification

All commands below were run from a clean checkout against this milestone's
final code, in this order:

1. `ruff check .` — clean, zero errors across the whole repository
   (application, tests, migrations).
2. `ruff format --check .` — clean, 199 files already formatted.
3. `mypy threnda tests migrations` (strict) — clean, zero errors across 177
   source files.
4. `pip check` (with the `dev` and `postgres` extras installed) — no broken
   requirements.
5. `pytest` — **636 tests, all passing**, including this milestone's new
   coverage: password/session/login unit tests
   (`tests/test_auth_service.py`), web login/logout/lockout/rate-limit/
   open-redirect tests (`tests/test_web_auth.py`), session-bound CSRF tests
   (`tests/test_csrf.py`), a role/IDOR permissions matrix across every
   workspace-scoped route (`tests/test_permissions_matrix.py`), CSV/JSON
   export schema/formula-injection/streaming/auth tests
   (`tests/test_exports.py`), webhook SSRF/signature/timeout/redirect/DNS
   tests against a real local HTTP server with monkeypatched DNS
   resolution (`tests/test_webhooks_service.py`), and the bootstrap CLI's
   one-time/fail-closed/weak-password/non-interactive behaviour
   (`tests/test_cli_bootstrap.py`). Every pre-existing Milestone 1–6 test
   was updated to the authenticated contract (session-derived
   `workspace_id` scoping, `actor` no longer client-supplied) rather than
   weakened or deleted.
6. A real Alembic `upgrade head` → `downgrade -1` → `upgrade head` round
   trip against a fresh SQLite database — clean.
7. A clean-database smoke test against a **real `uvicorn` process** (not
   `TestClient`): fresh SQLite database, `alembic upgrade head`, `threnda
   bootstrap-admin`, then a real HTTP walkthrough — login, workspace
   listing, source creation, a real file upload, exposure listing, the
   `/login` and `/exposures` web routes (200 and a 302-to-`/login` redirect
   respectively), a static asset, and the `Content-Security-Policy`/
   `X-Frame-Options`/`X-Content-Type-Options`/`Referrer-Policy` response
   headers all present — all correct.
8. A real `docker compose build && docker compose up -d` from a clean
   checkout (`docker compose down -v` first, so no volume/image state
   carried over) with `THRENDA_SECRET_KEY`/`POSTGRES_PASSWORD`/
   `THRENDA_BOOTSTRAP_ADMIN_USERNAME`/`THRENDA_BOOTSTRAP_ADMIN_PASSWORD`
   supplied via `.env` — both containers reached `healthy`, the entrypoint
   ran `alembic upgrade head` and the one-time bootstrap automatically
   (visible in `docker compose logs app`), and a full Definition-of-Done
   walkthrough was performed against the running container over real HTTP:
   login, workspace listing, a CSV import (with an explicit column
   mapping) and a SARIF import, filtering the queue by severity,
   acknowledging an exposure/assigning an owner/setting a remediation
   target date/changing its status, re-uploading the same CSV and
   observing `unchanged_findings: 5, new_findings: 0`, CSV and JSON
   export, the administrative audit log correctly recording
   `export_downloaded` (x2) and the bootstrap's own `member_added` event,
   the web queue page, a webhook creation attempt against a private
   address correctly refused (`"Webhook URL must use HTTPS in
   production."`, since `THRENDA_WEBHOOK_ALLOW_PRIVATE_TARGETS` is
   deliberately unset), and the members API. Container resource usage
   while idle: **~88 MB** for the app, **~29 MB** for PostgreSQL — well
   within CLAUDE.md's 16 GB Apple-Silicon target. `docker compose logs`
   was checked for any of "password"/"secret"/the test admin password
   string — none found. Finally `docker compose down -v` — clean shutdown,
   volumes removed.
9. This exact sequence (steps 6–8) also caught and fixed a real,
   independent bug: migration `32832b96f2c3`'s operation ordering was
   invalid against real PostgreSQL even though every SQLite-based test
   passed — see "Design decisions" above for the fix. This is the value of
   actually running the Docker/PostgreSQL path rather than trusting the
   SQLite-only test suite alone.

### Owner decisions / manual steps still outstanding

- **License.** Still not chosen — CLAUDE.md and README both still say so;
  no `LICENSE` file was added, per standing instructions.
- **Production secret provisioning.** `THRENDA_SECRET_KEY`/
  `POSTGRES_PASSWORD`/the bootstrap admin credentials must be generated
  and supplied by whoever actually deploys an instance — nothing in this
  repository invents or ships one, by design.
- **TLS termination.** Threnda's own container only ever serves plain HTTP
  on `127.0.0.1:8080`; docs/deployment.md documents putting a reverse
  proxy in front of it for anything beyond local access, but no reverse
  proxy is included in this repository (out of scope, and dependent on
  the owner's actual hostname/certificate situation).
- **Independent security review.** Nothing here has had a third-party
  security audit — see docs/security.md, "What this is not". The
  documented residual webhook DNS-rebinding race and the process-local
  (not multi-worker-aware) login rate limiter are both known, accepted
  trade-offs for Community's scope, not oversights, but the owner should
  make their own judgment call about whether either matters for a
  specific deployment.
- **Nothing was pushed, merged, or committed to `main`.** Work happened
  entirely on `feat/community-v0.1`, committed locally in small
  conventional commits (see `git log`) — pushing/merging/opening a PR is
  the owner's call, not done automatically here.

## Milestone 8 — self-contained Local AI

Status: implemented, pending owner review.

- [x] `EmbeddedAIProvider` is now the zero-configuration provider default;
      AI actions themselves remain explicit and optional.
- [x] Threnda manages a pinned llama.cpp `b10516` runtime and the official
      Qwen2.5 7B Instruct Q4_K_M GGUF model. macOS arm64/x64 and Linux
      arm64/x64 packages are selected automatically.
- [x] Runtime and model downloads are revision-pinned, byte-size checked and
      SHA-256 verified before use. Runtime archives are path-validated before
      extraction.
- [x] `threnda ai install`, `threnda ai status`, and an admin-only
      **Settings → Local AI** page provide installation, status and a
      synthetic connection test without requiring Ollama or a cloud account.
- [x] Offline bundles are accepted through `threnda ai install --bundle` or
      `THRENDA_AI_BUNDLE_DIR`, using the same verification path as downloads.
- [x] The managed llama-server binds only to loopback, starts lazily for an AI
      request, and is terminated by both FastAPI lifespan cleanup and an
      `atexit` guard.
- [x] Structured output remains schema-validated, bounded, visibly labelled
      and subject to mandatory human review. AI still cannot calculate the
      authoritative risk score or mutate/merge records on its own.
- [x] Ollama remains only as an explicitly selected developer compatibility
      provider; ordinary Community users do not need it.
- [x] Hermetic tests cover provider selection, loopback transport, tampered
      artifacts, archive traversal, unsupported platforms, the settings page
      and CSRF protection. A real Apple Silicon smoke test loaded the bundled
      model through llama.cpp/Metal and extracted a synthetic critical finding.
