# Finding lifecycle, roles and projects

This is the consultancy workflow Threnda supports for pilots:

```
finding entry -> assign owner & due date -> client remediates -> retest -> verified closure
(import/manual)                             ("fixed" in portal)   (pentester)
```

## Organisation model

| Concept | In Threnda | Notes |
| --- | --- | --- |
| Consultancy | the self-hosted instance | One instance = one consultancy. |
| Consultancy admin | `User.is_org_admin` | Manages clients and creates projects. Grants no project access by itself. |
| Client | `Client` | A customer of the consultancy. |
| Client project | `Workspace` with `client_id` | One engagement. Every finding, file, comment and member is scoped to it. |
| Client user | `User.client_id` + `client` role | Bound to exactly one client; can only be a member of that client's projects. |

The first account (`threnda bootstrap-admin`, or Desktop onboarding) is a
consultancy admin. The migration that introduced clients made every
existing workspace admin a consultancy admin.

## Roles (per project)

| Role (UI label) | Value | Can |
| --- | --- | --- |
| Consultancy admin | `admin` | Everything below, plus members, invitations, webhooks, audit log. |
| Pentester | `analyst` | Create/edit/import findings, owners and due dates, retests, internal comments and files, exports. |
| Viewer | `viewer` | Internal read-only (sees internal comments). |
| Client user | `client` | Client portal only: their project's findings, client-visible comments/files/history; comment, upload evidence, mark *in progress* or *fixed*. |

`client` is the lowest role: every route that requires `viewer` or above
(imports, sources, documents, exports, members, audit log, the consultancy
UI) refuses it. See [security.md](security.md#client-users).

## Statuses

| Status | Label | Who sets it |
| --- | --- | --- |
| `open` | Open | default; a failed retest; reopening a closed finding |
| `acknowledged` | Acknowledged | pentester/admin |
| `in_progress` | In progress | pentester/admin, or the client |
| `fix_reported` | Fixed — awaiting retest | the client (portal/API), or pentester/admin |
| `risk_accepted` | Risk accepted | pentester/admin |
| `resolved` | **Verified closed** | **only a passed retest** |

Rules enforced on the server (`threnda/app/services/exposures.py`,
`retests.py`, `collaboration.py`):

- No generic status change can set `resolved`; the API answers `409`.
  Verified closure always has a `Retest` row (who, when, notes) behind it.
- A **passed** retest sets `resolved` and `resolved_at`; a **failed**
  retest reopens the finding (`open`) and its notes are visible to the
  client.
- A client can only move a finding to `in_progress` or `fix_reported`.
- `fix_reported` and `resolved` are different statuses everywhere: queue,
  dashboard, overview, report and export.
- A finding that is missing from a later import is **not** closed. The
  import only records "not in latest import" on the source finding
  (timeline: *Not in latest import (source data only — status unchanged)*).
- Reopening: `resolved` / `risk_accepted` → `open` by a pentester/admin.

## Finding fields

title, description, severity, affected system, remediation recommendation,
owner (free text: person or team), due date, status — plus comments,
evidence files, retests and the history. Overdue = an active finding
(`open`, `acknowledged`, `in_progress`, `fix_reported`) whose due date is in
the past.

## Traceability

Every change to status, owner, due date, remediation, title, description,
severity and affected system writes an `ExposureHistory` row with the actor
and timestamp; comments, attachments and retests are append-only tables.
Security-relevant administrative events (accounts, memberships,
invitations, clients, projects, exports, attachment downloads, report views,
demo resets) go to the audit log. Clients see a filtered history: no
internal notes, no internal comments/files, no import bookkeeping.
