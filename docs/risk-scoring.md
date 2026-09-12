# Risk scoring (Milestone 4)

This document describes the deterministic, rule-based risk-scoring engine
as actually implemented — `threnda/app/services/risk_scoring.py` — per
`docs/community-v0.1-spec.md`, "RISK SCORING". Everything here is covered
by `tests/test_service_risk_scoring.py` (every input in isolation, plus
clamping boundaries) and the integration tests listed in
`docs/implementation-plan.md`, Milestone 4.

**Not AI-based.** The spec explicitly forbids using AI to produce the
authoritative score. This is a fixed table of rules, each independently
documented and testable — the whole point of "explainable."

## Inputs and their point contributions

Every component is computed even when its underlying signal is unknown —
an unknown input contributes `0` points and says so in its `detail` text,
rather than being silently omitted. This is what makes the breakdown
genuinely explain a score of, say, `2` for a critical-severity exposure
with nothing else known: every row is present, most just say "not known."

| Input | Values → points |
|---|---|
| **Severity** (`Exposure.severity`) | `informational` +2 · `low` +8 · `medium` +20 · `high` +35 · `critical` +50 |
| **CVSS** (max across linked findings) | `>= 9.0` +15 · `>= 7.0` +10 · `>= 4.0` +5 · `< 4.0` or unknown +0 |
| **Asset environment** (worst across linked assets) | `production` +15 · `staging` +5 · `development`/`test`/`unknown` +0 |
| **Internet exposure** (any linked asset known internet-facing) | known `true` +15 · known `false` −5 · unknown +0 |
| **Exploit evidence** (any linked finding reports it) | known `true` +20 · known `false` −5 · unknown +0 |
| **Business criticality** (worst across linked assets) | `critical` +15 · `high` +10 · `medium` +5 · `low`/`unknown` +0 |
| **Finding age** (days since `Exposure.first_seen`) | `>= 90d` +15 · `>= 30d` +10 · `>= 7d` +5 · `< 7d` +0 |

The raw sum is clamped to `[0, 100]` — the clamp is a ceiling/floor on the
*total*, not a per-component cap, so several stacked high signals can
still push the total to exactly 100 even though their sum exceeds it.

**Worked example**: a `critical` exposure (+50), max CVSS 9.8 (+15), on a
`production` (+15), internet-facing (+15) asset, with known exploit
evidence (+20), `critical` business criticality (+15), open 120 days
(+15) — raw sum 145, clamped to **100**. The same exposure with nothing
else known besides its `critical` severity scores **50**.

## Where each input comes from

- **Severity, age** — already on `Exposure` directly.
- **CVSS, exploit evidence** — `Finding.cvss_score` / `Finding.exploit_evidence`,
  aggregated (max / any-known-true) across every finding currently linked
  to the exposure. `exploit_evidence` is `None` unless the source
  explicitly reported it — only Threnda's own generic JSON importer has an
  optional key for this (`docs/importers.md`); CSV and SARIF have no
  public field for it, so their importers never set it.
- **Asset environment, internet exposure, business criticality** — via
  each linked finding's `Asset`, aggregated the same way (worst
  environment/criticality, any-known internet-facing). `internet_facing`
  and `business_criticality` are both manual-only: no importer sets
  either. Set them through `PATCH /api/v1/assets/{id}` or the exposure
  detail panel's Risk tab (which edits the same field, scoped to the
  asset(s) that exposure's findings actually link to).

**Only active findings count.** A finding the source no longer reports
(`Finding.resolved_at is not None` — see `docs/deduplication.md`, the
import differential engine) is excluded from aggregation, so a stale,
long-gone finding's CVSS/exploit evidence doesn't keep inflating a score
after the source itself stopped reporting it. If *every* linked finding
happens to be resolved, aggregation falls back to considering all of them
anyway — a still-open exposure never loses its severity/identity context
entirely just because its evidence aged out.

## Persistence and staleness

`Exposure.risk_score` (used for list sorting and the `min_risk_score`/
`max_risk_score` filters) is a persisted column, recomputed and written at
every point new signal can actually enter:

- an exposure is created (standalone, or via a possible-duplicate
  "keep separate" decision);
- a finding is linked to an exposure (auto-correlation, or a
  possible-duplicate "merge" decision);
- a linked finding's content changes on re-import (the `UPDATED`/
  `REOPENED`/absence-based-`RESOLVED` paths of the import differential
  engine);
- a linked asset's manual fields change (`PATCH /api/v1/assets/{id}`
  recomputes every exposure with a finding on that asset, not just one).

`GET /api/v1/exposures/{id}` does not read that persisted value — it calls
`compute_risk_score` fresh on every request, so the single-item view (and
its breakdown) is always exactly consistent with the exposure's current
linked findings/assets. The one thing this leaves imperfect on the *list*
view: the age-bucket component can silently cross a threshold with no
other mutation happening, since Community has no background worker to
periodically recompute every exposure. This is a documented limitation,
not a silent correctness bug — every event that legitimately changes the
score does trigger a recompute.

## Configuration

`RiskScoringConfig` (a frozen dataclass) holds every point value and
threshold above; `DEFAULT_RISK_CONFIG` is the table documented here.
Construction validates itself: every enum-keyed mapping must cover every
member of that enum (a config silently missing `SeverityLevel.LOW`, say,
is rejected rather than scoring those exposures as an unexplained `0`),
every point value must be a plain integer in `[-100, 100]`, the CVSS
thresholds must be ordered `low <= medium <= high <= 10`, and the age
buckets must start at `0` days and be strictly ascending. This is what
"configurable... without a rewrite" means in practice: a future
user-configuration feature constructs the same `RiskScoringConfig` class
from stored values and gets the identical validation for free — the
scoring engine (`compute_risk_score`) itself never changes.

## Lifecycle transitions

See `docs/implementation-plan.md`, Milestone 4, "Design decisions" for the
full lifecycle graph and the reasoning behind it being a DAG rather than
the acceptance criteria's literal single chain.
