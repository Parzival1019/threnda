# Independent ingestion acceptance checklist

This is a review checklist for Milestone 2, not a claim that ingestion exists.
Use temporary databases and synthetic fixtures. Do not touch other projects.

## Happy path and persistence

- Follow the documented fresh-clone setup without a pre-existing data directory.
- Provision a workspace and one source for each supported format through the
  documented local interface; do not require undocumented SQL shell operations.
- Upload the synthetic CSV, JSON and SARIF demos through the actual HTTP API.
- Reopen the application against the same database and verify import records,
  normalized findings and linked assets remain available.
- Verify workspace/source selection is consistent and foreign workspace IDs are
  rejected without writing any findings or altering the other workspace.
- Make current limitations explicit: cross-import deduplication, differentials,
  user authentication and the browser UI belong to later milestones.

## Untrusted input

- Bound request/upload memory and temporary storage, including requests without
  Content-Length. The size limit must not only run after an arbitrarily large
  multipart body has already been parsed. These are operational safety limits,
  not artificial limits on stored findings or assets.
- Never resolve an uploaded filename as a filesystem path. Test traversal-looking
  filenames, empty content, malformed encoding, nested JSON, invalid MIME claims,
  duplicate CSV headers, unexpected columns and missing required mapped fields.
- Use strict finite CVSS bounds and severity normalization; invalid values must
  produce actionable errors rather than NaN, 500 responses or silent truncation.
- Do not fetch any URL, schema, external reference, local path or command named in
  an uploaded file. Preserve useful references as inert data only.
- Ensure a failed or partially valid import has documented transaction semantics
  and a durable, accurate ImportRun summary. Never report success for discarded
  records, leak raw database errors or accidentally roll back its failure audit.
- Exercise CSV mapping explicitly and when reused. A saved mapping belongs to
  the intended source/workspace. Unknown or conflicting mappings are rejected.

## SARIF interoperability

Read the public [OASIS SARIF 2.1.0 specification with errata](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html),
particularly sections 3.27 and 3.50, before interpreting scanner output.

Test multiple runs, rule IDs and rule-index lookup, an explicit result level,
rule-default severity when the result omits it, physical artifact locations,
help links and preservation of producer fingerprint information. Do not treat
every SARIF result as a failing security issue: handle or explicitly reject
non-failure kinds. Document unsupported constructs instead of guessing a
vendor-specific meaning. Fingerprint stability must not depend on absolute line
numbers, and partial fingerprint names must not acquire invented semantics.

## Verification gate

Run all tests, independent foundation regressions, Ruff lint/format and mypy.
Review the real Alembic upgrade path, not just metadata.create_all. Keep valid
existing records and history intact. Commit only locally on feat/community-v0.1;
do not push or merge as part of this milestone.
