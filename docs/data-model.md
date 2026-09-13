# Data model

The full SQLAlchemy 2 schema (`threnda/app/models/`), current as of
Milestone 7. Every table below except `users` and `user_sessions` is scoped
to a `Workspace`, and — where another table references it — via a
workspace-scoped **composite foreign key** (`(workspace_id, id)`), not a bare
integer id: this is what lets the database itself reject a row that
references a real id belonging to a *different* workspace, including from
raw SQL, not just ORM-level validation. See
`docs/implementation-plan.md`, Milestone 1's "Independent review fixes" for
why this exists.

Enums are stored as `VARCHAR` + `CHECK` (`native_enum=False,
create_constraint=True`), not a native database enum type — portable across
SQLite/PostgreSQL and simpler to extend later. Timestamps use a custom
`UTCDateTime` column type that always round-trips a timezone-aware UTC
`datetime`, on either database.

## Core domain (Milestones 1–6)

### `Workspace`
`id`, `name` (unique), `created_at`. The multi-tenancy boundary — everything
else belongs to exactly one workspace.

### `Source`
`id`, `workspace_id`, `name`, `source_type` (csv/json/sarif/pentest_report/
trivy/nuclei/semgrep/other), `meta` (JSON; column name is `metadata` — see
below), `created_at`.

> **Naming note**: the spec calls this field `metadata`, which collides with
> `DeclarativeBase.metadata` on every SQLAlchemy model. The Python
> attribute/JSON API field is `meta`; the database column is still literally
> named `metadata`.

### `ImportRun`
`id`, `workspace_id`, `source_id`, `filename`, `file_hash`, `started_at`,
`completed_at`, `status` (pending/running/completed/failed),
`total_records`, `successful_records`, `failed_records`, plus five
differential-engine counters: `new_findings`, `unchanged_findings`,
`updated_findings`, `resolved_findings`, `reopened_findings`.

### `Asset`
`id`, `workspace_id`, `canonical_key`, `name`, `asset_type` (host/ip/url/
application/repository/container_image/container/kubernetes_workload/
cloud_resource/unknown), `environment` (production/staging/development/
test/unknown), `internet_facing` (nullable bool, manual, risk-scoring input),
`business_criticality` (manual, risk-scoring input), `meta` (JSON),
`first_seen`, `last_seen`.

### `Finding`
The normalized unit of evidence. `id`, `workspace_id`, `fingerprint`
(cross-source correlation signal — see
[deduplication.md](deduplication.md)), `source_id`, `source_finding_id`
(source-local identity the differential engine matches on — deliberately a
*different* field from `fingerprint`), `title`, `description`, `severity`,
`cvss_score`, `cve`, `cwe`, `asset_id`, `exposure_id`, `evidence`,
`remediation`, `exploit_evidence` (nullable bool, risk-scoring input),
`references` (JSON list), `raw_data` (JSON — the original record, for
traceability), `first_seen`, `last_seen`, `resolved_at`, `created_at`,
`updated_at`.

### `Exposure`
The correlated, user-facing issue — many `Finding`s may point at one
`Exposure`. `id`, `workspace_id`, `canonical_fingerprint`, `title`,
`description`, `severity`, `risk_score` (0–100), `status` (open/
acknowledged/in_progress/risk_accepted/resolved), `owner`, `acknowledged`,
`remediation_target_date`, `remediation`, `first_seen`, `last_seen`,
`resolved_at`, `created_at`, `updated_at`.

### `ExposureHistory`
Append-only audit trail of every meaningful `Exposure` change. `id`,
`exposure_id`, `event_type`, `previous_value`, `new_value`, `actor` (a plain
string — before Milestone 7 a caller-supplied placeholder, now always the
authenticated username), `timestamp`. Distinct from the Milestone 7
`AuditLogEntry` table below, which covers administrative/security events,
not routine finding workflow.

### `PossibleDuplicate`
A pending Layer-2 fuzzy-match review candidate. `id`, `workspace_id`,
`finding_id`, `candidate_exposure_id`, `similarity_score`, `status`
(pending/merged/kept_separate), `created_at`, `resolved_at`, `resolved_by`.

### `Document` / `CandidateFinding`
Local pentest-report text extraction and its human-review queue — see
[pentest-and-ai.md](pentest-and-ai.md) for the full design.
`Document`: `id`, `workspace_id`, `source_id`, `filename`, `content_type`
(pdf/text), `file_hash`, `status` (processed/failed), `error_message`,
`extracted_text`, `page_count`, `created_at`.
`CandidateFinding`: `id`, `workspace_id`, `document_id`, `status` (pending/
accepted/rejected), `extracted_title`, `extracted_severity`, `excerpt`,
`page_number`, `line_start`, `line_end`, `confidence`, `validation_issues`
(JSON list), `ai_assisted`, `ai_summary`, `ai_model`, `resulting_finding_id`,
`created_at`, `resolved_at`, `resolved_by`.

## Authentication, authorization, and administration (Milestone 7)

### `User`
`id`, `username` (unique, normalized lowercase), `password_hash` (Argon2id),
`is_active`, `failed_login_count`, `locked_until`, `created_at`,
`updated_at`. Global — not workspace-scoped; access is granted per-workspace
through `WorkspaceMembership`.

### `WorkspaceMembership`
`id`, `workspace_id`, `user_id`, `role` (admin/analyst/viewer), `created_at`.
Unique on `(workspace_id, user_id)` — one role per user per workspace. The
sole source of authorization for every workspace-scoped route — see
[security.md](security.md), "Authorization".

### `UserSession`
`id`, `user_id`, `token_hash` (SHA-256 of the opaque session token — the
plaintext token itself is never stored), `created_at`, `last_seen_at`,
`expires_at`, `revoked_at`, `user_agent`, `ip_address`. See
[security.md](security.md), "Sessions".

### `AuditLogEntry`
`id`, `workspace_id` (nullable — some events have no workspace context),
`actor_user_id` (nullable, `ON DELETE SET NULL`), `actor_username`
(denormalized snapshot), `event_type`, `target_type`, `target_id`, `detail`
(JSON — never a secret, enforced by the single write path,
`threnda.app.services.audit.record_audit_event`), `ip_address`,
`created_at`. Immutable; nothing ever updates or deletes a row here.

### `WebhookEndpoint`
`id`, `workspace_id`, `url`, `description`, `secret_ciphertext` (Fernet,
never plaintext at rest, never returned after creation), `enabled`,
`created_by_user_id` (nullable, `ON DELETE SET NULL`), `created_at`,
`updated_at`, `last_triggered_at`, `last_status_code`, `last_error`. See
[webhooks.md](webhooks.md).

## Entity relationship summary

```
Workspace 1──* Source 1──* ImportRun
Workspace 1──* Asset
Workspace 1──* Finding *──1 Source
Finding *──1 Asset (nullable)
Finding *──1 Exposure (nullable)      Exposure 1──* ExposureHistory
Workspace 1──* Exposure
Workspace 1──* PossibleDuplicate ──1 Finding, ──1 Exposure(candidate)
Workspace 1──* Document 1──* CandidateFinding ──1 Finding (once accepted)
Workspace 1──* WorkspaceMembership *──1 User 1──* UserSession
Workspace 1──* AuditLogEntry *──1 User (nullable)
Workspace 1──* WebhookEndpoint
```
