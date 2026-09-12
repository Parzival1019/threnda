# Independent Milestone 3 review

Reviewed commit: `f253b9e`.

Baseline verification: 158 tests pass; Ruff lint and format checks pass;
strict mypy passes for `threnda` and `tests`; dependencies are consistent.
Real HTTP imports were exercised against a database populated at Milestone 2
and then upgraded to Milestone 3. Fix the following before Milestone 4.

1. **Legacy findings remain outside the Exposure queue.** Upgrading a real
   Milestone 2 database with 11 unlinked findings preserved the rows but created
   no Exposure or review candidate for any of them. A follow-up import created
   one new Exposure, so `GET /exposures` returned one item while eleven stored
   findings remained unreachable from the user-facing queue. Safely backfill all
   pre-Milestone-3 unlinked findings during the upgrade (including deterministic
   cross-source correlation where safe), or provide an automatic idempotent
   equivalent. Never overwrite existing links or auto-merge POSSIBLE matches.
2. **Stable source IDs do not survive title changes.** Two generic JSON imports
   used the same explicit `id` but changed only the title. The second run reported
   `NEW=1, RESOLVED=1` instead of updating the existing row. Source-local identity
   and cross-source correlation identity are distinct concepts: use a trustworthy
   producer ID where the format supplies one, with a documented fallback for CSV
   and other formats that do not. A content field that is allowed to update must
   not itself make the source-local identity unstable.
3. **Partial parse errors can falsely resolve active findings.** Starting with two
   valid JSON findings, a later import containing one valid record and one invalid
   version of the second recorded `failed_records=1` but also
   `resolved_findings=1`. An incomplete snapshot cannot prove absence. Do not
   infer RESOLVED from an import with row errors unless the importer can prove the
   missing identity independently; the safe general rule is to suppress absence
   resolution for that run and document it.
4. **Duplicate identities inside one upload return HTTP 500.** A JSON document
   with two identical normalized records violates the database unique constraint,
   rolls the request back and leaves no ImportRun. Detect collisions before writes
   and return an actionable, durable result (a FAILED 422 import is acceptable),
   without partial findings or a raw database error.
5. **Per-finding differential history is not preserved.** Aggregate ImportRun
   counters say how many records resolved or reopened, but not which finding did
   so; `Finding.import_run_id` is overwritten each time and `resolved_at` only
   represents current state. Record durable per-finding NEW/UNCHANGED/UPDATED/
   RESOLVED/REOPENED observations, or at minimum audit RESOLVED/REOPENED against
   linked exposures using the existing event types. The result must allow a later
   user to answer which finding changed in a particular import without relying on
   mutable current rows.

All reproduced data was synthetic. Add migration-backed and actual upload-path
tests, run the full verification suite, and commit locally on
`feat/community-v0.1`. Do not push or merge.
