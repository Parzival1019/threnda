# Independent Milestone 1 review

Reviewed commit: `28856de`.

Baseline verification: 37 tests pass; Ruff lint and format checks pass; mypy passes.
The following failures were reproduced independently using temporary SQLite databases
and synthetic data. Fix these before continuing to ingestion, with regression tests.

1. **Cross-workspace references are accepted.** A Finding with workspace A and a
   source in workspace B successfully commits. ImportRun has the same independent
   workspace/source FKs, and Finding's asset/exposure/import links can disagree too.
   Enforce the invariant in persisted data (including raw SQL writes), cover optional
   relationships and source/import consistency, and keep the ORM usable. Add an
   Alembic migration and prove that valid existing data survives an upgrade. Do not
   silently delete or rewrite invalid data to make an upgrade pass.
2. **Null PATCH values crash.** PATCH an existing exposure with `{"status": null}`
   or `{"acknowledged": null}` returns HTTP 500. Distinguish omission from null:
   reject explicit null for non-nullable fields with 422, while allowing owner and
   deadline to be cleared. Validate relevant string lengths, and keep failed writes
   atomic with no partially persisted updates/audit entries.
3. **Audit trail omits successful changes.** Changing acknowledged from true to
   false and changing remediation both return 200 but create no history. Audit both
   directions of meaningful changes with old/new values; retain no-op idempotency.
   Avoid contradictory acknowledgement/status behavior and document that decision.
4. **Enums are not DB-constrained despite documentation.** Raw SQL can set Finding
   severity to `not-a-valid-severity`. The SAEnum helper lacks create_constraint=True.
   Enforce and migrate the documented enum constraints; cover invalid raw values
   through real migrated schemas, not only metadata.create_all.
5. **Health reports an unusable database as healthy.** A fresh empty SQLite file
   (no migrations) returns HTTP 200 with status/database ok. Make readiness fail
   with 503 for inaccessible/uninitialized/outdated databases while returning no
   secrets or raw DB error text; distinguish this from simple process liveness if
   needed. Add migration-backed health tests.
6. **The documented fresh-clone startup fails.** README runs `alembic upgrade
   head` with the default `sqlite:///./data/threnda.db`, but `data/` is absent
   in a clean checkout. Reproduced with an absent parent directory: Alembic exits
   1 with `unable to open database file`. Make the quickstart reproducible (an
   explicit `mkdir -p data` is sufficient), and verify every documented command
   from a clean local setup. No need for broad automatic directory creation.

Public references used to validate the review approach:

- https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#foreign-key-support
- https://docs.sqlalchemy.org/en/20/core/type_basics.html#sqlalchemy.types.Enum
- https://fastapi.tiangolo.com/advanced/testing-events/

After fixing, rerun the entire test suite, Ruff lint/format and mypy. Keep the
follow-up in a separate local commit and update the implementation plan accurately.
Do not push or merge. The caller-supplied workspace/actor remains an explicitly
documented local-only limitation until authentication; it is not authorization.
