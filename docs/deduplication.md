# Deduplication and import differentials (Milestone 3)

This document describes the deduplication/correlation engine and the import
differential engine as actually implemented, per
`docs/community-v0.1-spec.md`'s "DEDUPLICATION" and "IMPORT DIFFERENTIALS"
sections. Nothing here is aspirational — every behaviour described is
covered by a test in `tests/test_dedup_similarity.py`,
`tests/test_dedup_correlation.py`, `tests/test_differential_engine.py`,
`tests/test_api_possible_duplicates.py`, `tests/test_csv_reorder_stability.py`,
and `tests/test_migration_backfill.py`.

An independent review (`docs/milestone-3-review.md`) against a real migrated
database and real HTTP uploads found five defects in the first cut of this
milestone; this revision of the document reflects the fixes, not the
original design. The most consequential fix — separating the two identity
concepts below — touched the differential engine's matching key, the schema
constraint that enforces it, every importer, and required a data backfill
for pre-Milestone-3 databases; see "Independent review fixes" at the end.

## Two separate identities, two separate problems

Threnda draws a hard line between two questions that are easy to conflate,
and conflating them was the root cause of the review's most serious finding
(a title edit on an otherwise-unchanged finding being misread as "resolved,
then a new one appeared" — see "Independent review fixes" below):

1. **"Have I seen this exact finding before, from this exact source?"** —
   answered by the **import differential engine**
   (`threnda.app.services.ingestion.runner.persist_parsed_import`) on every
   re-import of a given `Source`, keyed on `Finding.source_finding_id` — the
   **source-local identity** an importer assigns (a producer-supplied id for
   JSON/SARIF; a documented content-derived fallback for CSV — see "Source-
   local identity per format" below). This never creates duplicate rows.
2. **"Is this a new finding I haven't correlated yet, and if so, which
   `Exposure` — if any — does it belong to?"** — answered by the
   **correlation engine** (`threnda.app.services.dedup.correlation`),
   exactly once, the first time the differential engine decides a finding
   is genuinely `NEW`, using `Finding.fingerprint` — the **cross-source
   correlation signal**.

These two fields are deliberately allowed to diverge: `source_finding_id`
must stay stable when identifying content (e.g. a title) changes, while
`fingerprint` is *supposed* to change when identifying content changes,
because it feeds correlation against other sources. Baking one into the
other (the original bug) makes a mere content edit look like the finding
disappeared and a different one appeared.

A finding is only ever run through correlation once, at creation. Once it
has an `exposure_id` (or a pending `PossibleDuplicate` row), later
re-imports never move it to a different exposure automatically — that
would silently undo a decision a human (or the engine) already made.

### Source-local identity per format

- **JSON**: the producer's own `"id"` field when given, else `row-{index}`
  for a document that supplies none at all (a positional fallback is
  acceptable here only because there is no better signal *and* JSON's own
  `"id"` is the common, recommended case).
- **SARIF**: `partialFingerprints` (verbatim, sorted) first, then
  `ruleId@artifactUri`, and only as a genuine last resort a positional
  `run{N}-result{M}` — real producers essentially always supply at least a
  rule and a location.
- **CSV**: no producer-supplied identity exists at all. The identity is a
  content signature over the same fields `fingerprint` uses (CVE + asset +
  title) — identical to `fingerprint` for this format. This is a deliberate,
  documented trade-off, not an oversight: it makes identity stable across a
  re-exported file with rows in a different order (which a positional
  `row-{index}` fallback would *not* survive — see
  `tests/test_csv_reorder_stability.py`), at the honest cost that editing a
  CSV row's title/CVE/asset is indistinguishable from "resolved, then a new
  one appeared" — there is nothing else to prove otherwise for this format.

## Import differentials

`persist_parsed_import` classifies every record from a parsed import
against the existing `Finding` rows for that exact `(workspace_id,
source_id)`, keyed on `Finding.source_finding_id` (the source-local
identity — see above, **not** `fingerprint`):

| Existing state for this identity          | Outcome     | Effect |
|-----------------------------------------------|-------------|--------|
| No finding at all                             | `NEW`       | Insert a new `Finding`, run it through correlation. |
| Active finding (`resolved_at IS NULL`), content unchanged | `UNCHANGED` | Refresh `last_seen`; no correlation re-run. |
| Active finding, content differs                | `UPDATED`   | Overwrite content fields (including `fingerprint`, which is allowed to change) in place; refresh `last_seen`; no correlation re-run. |
| Resolved finding (`resolved_at IS NOT NULL`) reappears | `REOPENED`  | Clear `resolved_at`; overwrite content; refresh `last_seen`; keeps its *original* `exposure_id` — no correlation re-run; audited (see "Per-finding lifecycle audit" below). |
| Active finding present in a *previous* import of this source, absent from this one | `RESOLVED`  | Set `resolved_at`; the row is never deleted; audited. Only inferred when this run had zero row errors — see below. |

Counts for all five outcomes are stored directly on the triggering
`ImportRun` (`new_findings`, `unchanged_findings`, `updated_findings`,
`resolved_findings`, `reopened_findings`) — that is the aggregate view of
the differential engine's history: every import run is a permanent,
queryable record of what changed, and no `Finding` row is ever deleted by
an import. `RESOLVED` and `REOPENED` are simply two directions across the
same `resolved_at` timestamp.

### Duplicate identities within one import

Two records in the *same* upload sharing a `source_finding_id` would both
try to become "the" row for that identity, colliding on
`uq_findings_workspace_source_finding_id` on the second insert.
`find_duplicate_source_finding_ids` checks for this before anything is
written; if found, `persist_parsed_import` raises `DuplicateIdentityError`
before creating even the `ImportRun` row. The upload API
(`threnda.app.api.v1.imports`) catches this exactly like a whole-file
`ImportFormatError`: it records a durable `FAILED` `ImportRun` and reports
`422`, never a raw database error and never a partial write.

### Row errors suppress absence-based RESOLVED for that run

A record that fails to normalize never reaches `parsed.findings` at all —
its identity can't be told apart from "genuinely gone from the source" in
that case. If an import has *any* row errors, the RESOLVED-by-absence step
is skipped entirely for that run (every successfully-parsed record is still
fully processed as NEW/UNCHANGED/UPDATED/REOPENED either way): inferring
RESOLVED for every active identity a partially-broken run didn't happen to
see would risk falsely resolving a finding whose only problem was a
malformed row that time, not a genuine absence.

### Per-finding lifecycle audit

Aggregate `ImportRun` counters answer "how many findings resolved this
run", not "which ones" — not enough to reconstruct history later. Every
RESOLVED/REOPENED transition for a finding that has a linked exposure is
also recorded as an `ExposureHistory` entry (`event_type=RESOLVED` /
`REOPENED`, `new_value="finding_id={id}"`), so a later reader can answer
"which finding changed in this import" from the exposure's own audit trail,
not just its current mutable `resolved_at`/`exposure_id` columns. A finding
with no linked exposure yet (e.g. still pending POSSIBLE review) is simply
not audited this way — there's no exposure to attach the entry to.

Per-row parse errors (already isolated by the importer into
`ParsedImport.errors`) are counted in `ImportRun.failed_records` exactly as
in Milestone 2 — this did not change.

### What resolving a finding does *not* do

Setting `Finding.resolved_at` never touches `Exposure.status`. Automated
ownership / automated workflow transitions are listed in `CLAUDE.md` as an
Enterprise-only capability; Community's `Exposure.status` remains a
user-controlled workflow field. A resolved finding still shows up under its
exposure — a human decides whether the exposure itself should move to a
closed state, Community does not infer that from source data alone.

## Correlation (Layer 1 + Layer 2)

`correlate_new_finding(db, finding)` runs exactly once per finding, only
when the differential engine has just classified it as `NEW`.

### Layer 1 — exact fingerprint match

If another `Finding` in the same workspace already has the identical
fingerprint, already has an `exposure_id`, and comes from a *different*
`Source` (the same-source, same-identity case is entirely the differential
engine's `UNCHANGED`/`UPDATED` path above, keyed on `source_finding_id`, not
`fingerprint`), the new finding is linked to that same `Exposure`
automatically. This is the case of two different tools independently
reporting a fingerprint that normalizes to the same identifying signal
(e.g. the same CVE against the same asset).

### Layer 2 — fuzzy title similarity

If Layer 1 finds nothing, candidate exposures are gathered by a **shared
strong signal** — another finding in the same workspace sharing the new
finding's CVE, or sharing its asset — and each candidate's title is
compared against the new finding's title with deterministic text
similarity (`difflib.SequenceMatcher`, stdlib only, see
`threnda.app.services.dedup.similarity.title_similarity`). Titles are
normalized first (lowercased, punctuation stripped, whitespace collapsed)
so formatting differences don't affect the score.

Comparing every new finding's title against every exposure in the
workspace would be both slow at scale and prone to matching titles that
merely sound alike but share nothing else — the shared-signal gate exists
specifically to avoid that.

Two thresholds, defined in `threnda.app.services.dedup.correlation`:

```python
HIGH_SIMILARITY_THRESHOLD = 0.92
POSSIBLE_SIMILARITY_THRESHOLD = 0.72
```

| Confidence | Similarity range   | Behaviour |
|------------|---------------------|-----------|
| `EXACT`    | n/a (Layer 1)        | Auto-correlated. |
| `HIGH`     | `>= 0.92`            | Auto-correlated. |
| `POSSIBLE` | `>= 0.72` and `< 0.92` | **Never auto-merged.** A `PossibleDuplicate` row is created; the finding is left with no `exposure_id` until a human resolves it. |
| (none)     | `< 0.72`, or no shared signal at all | A new standalone `Exposure` is created for the finding. |

### Layer 3 — semantic matching

Not implemented. The spec gates it on local AI/embedding capability, which
Community does not require and does not enable by default (see
`CLAUDE.md`, "Optional local AI"). Revisit only if/when Milestone 6 adds
that capability.

### The POSSIBLE review queue never auto-resolves itself

This is the one hard rule this whole engine exists to enforce, stated
explicitly in the governing task for this milestone: **a `POSSIBLE` match
is never automatically merged, under any circumstance.** Concretely:

- `correlate_new_finding` has no code path that sets `exposure_id` for a
  `POSSIBLE`-band score — it only ever creates a `PossibleDuplicate` row.
- Re-importing the same source again does not re-run correlation on a
  finding that already exists (see "Two separate problems" above), so a
  pending candidate cannot be silently resolved by a later import either.
- The only two ways a `PossibleDuplicate` row's `status` ever changes are
  the two API actions below, both of which require an explicit HTTP call
  naming that specific candidate.

`tests/test_dedup_correlation.py::test_possible_similarity_creates_review_candidate_and_never_auto_merges`
and `tests/test_api_possible_duplicates.py::test_nothing_ever_auto_merges_a_possible_candidate`
assert this directly.

## The possible-duplicate review API

`threnda/app/api/v1/possible_duplicates.py`, mounted at
`/api/v1/possible-duplicates`:

- `GET /api/v1/possible-duplicates?workspace_id=...` — paginated list,
  scoped to a workspace, defaulting to `status=pending` only (pass
  `status=merged` or `status=kept_separate` to see resolved history).
- `POST /api/v1/possible-duplicates/{id}/merge?workspace_id=...` — a human
  confirms the candidate: links the finding to the candidate exposure via
  the same `link_finding_to_exposure` helper Layer 1/2 auto-correlation
  uses (recorded with `confidence="possible-merged"` in the
  `ExposureHistory` audit trail, so the audit log always shows whether a
  correlation was automatic or a reviewed decision), then marks the
  candidate `merged`.
- `POST /api/v1/possible-duplicates/{id}/keep-separate?workspace_id=...` —
  a human rejects the candidate: the finding gets its own new standalone
  `Exposure` (via `create_standalone_exposure`, the same helper used when
  correlation finds no match at all), then marks the candidate
  `kept_separate`.

Both actions are scoped to `workspace_id` (a candidate belonging to another
workspace resolves as `404`, not leaked), and both are guarded against
double-resolution: once a candidate is no longer `pending`, either action
returns `409` instead of silently no-op'ing or re-applying the decision.

There is deliberately no bulk-merge or auto-merge-above-some-score
endpoint. Every resolution here is one explicit, individually-recorded
human decision — that is the entire point of the confidence tier existing
at all.

## Schema

- `findings`: `resolved_at` (nullable) — set by the differential engine,
  distinct from `Exposure.status`, which stays user-controlled. New
  `UniqueConstraint("workspace_id", "source_id", "source_finding_id")`,
  named `uq_findings_workspace_source_finding_id` — the invariant the
  differential engine's active/resolved lookup relies on. Deliberately not
  on `fingerprint`, which is allowed to change with content (see above).
- `import_runs`: five new integer counters, `new_findings` through
  `reopened_findings`, all `NOT NULL DEFAULT 0`.
- `possible_duplicates` (new table): `finding_id` and `candidate_exposure_id`
  (each a composite FK into `findings`/`exposures` scoped by
  `workspace_id`, consistent with every other cross-table reference in this
  schema), `similarity_score`, `status` (`pending` / `merged` /
  `kept_separate`), `resolved_at`, `resolved_by`.

Migration `32832b96f2c3` adds all of the above. It runs a pre-flight
read-only check (`_assert_no_duplicate_source_finding_ids`) before any DDL,
because a database that hit the title-change bug (see below) before this
migration could already have more than one finding sharing a
(workspace_id, source_id, source_finding_id) — which the new unique
constraint would reject. If any are found, the migration raises before
touching the schema, leaving the database untouched; there is no
partial-upgrade state to recover from, and the migration does not attempt
to automatically pick a winner between the duplicate rows (that would mean
inventing merge semantics the spec reserves for human review).

### Legacy backfill into the queue

The same migration also runs every pre-existing `Finding` with
`exposure_id IS NULL` through `correlate_new_finding` — the identical
function and thresholds a live import's genuinely new finding goes through,
not a separate "migration mode". This is what gets a database populated
under Milestone 1/2 (before anything ever ran those rows through
correlation at all) into the Exposure/review queue: deterministic EXACT/
HIGH matches auto-correlate (including across the legacy rows themselves,
processed in ascending `id` order, so two legacy findings sharing a
fingerprint correctly land on the same exposure), uncertain POSSIBLE
matches create a review candidate and are never auto-merged, and a finding
with no signal at all gets its own standalone exposure.

The backfill is idempotent and never overwrites an existing link: it only
ever considers findings with `exposure_id IS NULL` that don't already have
a `PossibleDuplicate` row, so a finding already linked or already flagged
for review — by this backfill or by anything else — is never touched or
re-flagged again. See `tests/test_migration_backfill.py`.

## Independent review fixes (post-first-cut, this revision)

An independent review (`docs/milestone-3-review.md`) against a real
migrated database and real HTTP uploads found five defects, all fixed in
the same change that produced this revision of the document:

1. **Legacy findings never entered the queue.** Fixed by the backfill
   described above.
2. **A stable source-local id didn't survive a content edit** (e.g. a JSON
   `"id"` unchanged, only the title edited) — the differential engine
   matched on `fingerprint`, which baked the title into the identity.
   Fixed by matching on `source_finding_id` instead everywhere (see "Two
   separate identities" above) and giving CSV a reorder-stable, documented
   fallback identity instead of a positional one.
3. **A partially-erroring import could falsely resolve an untouched
   finding.** Fixed by suppressing absence-based RESOLVED inference for any
   run with row errors (see "Row errors suppress absence-based RESOLVED"
   above).
4. **Duplicate identities in one upload raised a raw database error (500).**
   Fixed by detecting the collision before any write and reporting a
   durable `FAILED` `422` (see "Duplicate identities within one import"
   above).
5. **Aggregate counters couldn't answer which finding changed.** Fixed by
   auditing every RESOLVED/REOPENED transition against its linked exposure
   (see "Per-finding lifecycle audit" above).
