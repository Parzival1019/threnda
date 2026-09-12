# Deduplication and import differentials (Milestone 3)

This document describes the deduplication/correlation engine and the import
differential engine as actually implemented, per
`docs/community-v0.1-spec.md`'s "DEDUPLICATION" and "IMPORT DIFFERENTIALS"
sections. Nothing here is aspirational — every behaviour described is
covered by a test in `tests/test_dedup_similarity.py`,
`tests/test_dedup_correlation.py`, `tests/test_differential_engine.py`, and
`tests/test_api_possible_duplicates.py`.

## Two separate problems

Threnda draws a hard line between two questions that are easy to conflate:

1. **"Have I seen this exact finding before, from this exact source?"** —
   answered by the **import differential engine**
   (`threnda.app.services.ingestion.runner.persist_parsed_import`) on every
   re-import of a given `Source`. This never creates duplicate rows and
   never needs fuzzy matching, because the fingerprint from the *same
   source* is expected to be byte-for-byte stable across re-imports.
2. **"Is this a new finding I haven't correlated yet, and if so, which
   `Exposure` — if any — does it belong to?"** — answered by the
   **correlation engine** (`threnda.app.services.dedup.correlation`),
   exactly once, the first time the differential engine decides a finding
   is genuinely `NEW`.

A finding is only ever run through correlation once, at creation. Once it
has an `exposure_id` (or a pending `PossibleDuplicate` row), later
re-imports never move it to a different exposure automatically — that
would silently undo a decision a human (or the engine) already made.

## Import differentials

`persist_parsed_import` classifies every record from a parsed import
against the existing `Finding` rows for that exact `(workspace_id,
source_id)`, keyed on `Finding.fingerprint`:

| Existing state for this fingerprint          | Outcome     | Effect |
|-----------------------------------------------|-------------|--------|
| No finding at all                             | `NEW`       | Insert a new `Finding`, run it through correlation. |
| Active finding (`resolved_at IS NULL`), content unchanged | `UNCHANGED` | Refresh `last_seen`; no correlation re-run. |
| Active finding, content differs                | `UPDATED`   | Overwrite content fields in place; refresh `last_seen`; no correlation re-run. |
| Resolved finding (`resolved_at IS NOT NULL`) reappears | `REOPENED`  | Clear `resolved_at`; overwrite content; refresh `last_seen`; keeps its *original* `exposure_id` — no correlation re-run. |
| Active finding present in a *previous* import of this source, absent from this one | `RESOLVED`  | Set `resolved_at`; the row is never deleted. |

Counts for all five outcomes are stored directly on the triggering
`ImportRun` (`new_findings`, `unchanged_findings`, `updated_findings`,
`resolved_findings`, `reopened_findings`) — that is the differential
engine's history: every import run is a permanent, queryable record of what
changed, and no `Finding` row is ever deleted by an import. `RESOLVED` and
`REOPENED` are simply two directions across the same `resolved_at`
timestamp.

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
`Source` (the same-source, same-fingerprint case is entirely the
differential engine's `UNCHANGED`/`UPDATED` path above, and is in fact
prevented from reaching correlation at all by the
`uq_findings_workspace_source_fingerprint` constraint), the new finding is
linked to that same `Exposure` automatically. This is the case of two
different tools independently reporting a fingerprint that normalizes to
the same identifying signal (e.g. the same CVE against the same asset).

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
  `UniqueConstraint("workspace_id", "source_id", "fingerprint")` — the
  invariant the differential engine's active/resolved lookup relies on.
- `import_runs`: five new integer counters, `new_findings` through
  `reopened_findings`, all `NOT NULL DEFAULT 0`.
- `possible_duplicates` (new table): `finding_id` and `candidate_exposure_id`
  (each a composite FK into `findings`/`exposures` scoped by
  `workspace_id`, consistent with every other cross-table reference in this
  schema), `similarity_score`, `status` (`pending` / `merged` /
  `kept_separate`), `resolved_at`, `resolved_by`.

Migration `32832b96f2c3` adds all of the above. It runs a pre-flight
read-only check (`_assert_no_duplicate_fingerprints`) before any DDL,
because a Milestone-2-era database could already contain rows that would
violate the new `uq_findings_workspace_source_fingerprint` constraint —
Milestone 2's importer never checked for that. If any are found, the
migration raises before touching the schema, leaving the database
untouched; there is no partial-upgrade state to recover from.
