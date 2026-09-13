# Architecture

Threnda Community is a **modular monolith**: one Python process (FastAPI +
Uvicorn), one relational database (SQLite by default, PostgreSQL-compatible),
no queue, no cache, no separate services — see CLAUDE.md's "Architecture
principles" for why, and what would have to change before that's no longer
true (nothing has, as of this milestone).

## Process shape

```
                        ┌─────────────────────────────┐
 browser / API client → │        FastAPI process       │ → SQLite or PostgreSQL
                        │  (Uvicorn, one worker)       │
                        └─────────────────────────────┘
                                     │
                          optional, disabled by default
                                     ▼
                          local Ollama (loopback only)
```

There is no background worker anywhere. Everything — ingestion, deduplication,
risk scoring, PDF text extraction, webhook delivery — happens synchronously
inside the HTTP request that triggers it. This is a deliberate simplicity
trade-off (CLAUDE.md: "prefer simpler architecture... avoid unnecessary
abstractions") appropriate to Community's scale target (a security team's
local findings queue, not a multi-tenant SaaS).

## Code layout

```
threnda/app/
  main.py            create_app(): builds the FastAPI app, engine, sessionmaker
  core/
    config.py        Settings (env-var driven), see .env.example
    startup_checks.py  fail-closed production checks
    middleware.py    request-size cap, security response headers
  db/
    session.py       engine/sessionmaker construction, per-request session
    migration_status.py  is the schema actually at head?
  models/            SQLAlchemy 2 ORM models — the persistent domain model
  schemas/           Pydantic v2 request/response models for the JSON API
  services/          business logic, callable from both the API and the web UI
    ingestion/       importer interface + CSV/JSON/SARIF/pentest-JSON
    dedup/           fingerprinting, similarity, correlation
    documents/       local PDF/text extraction, candidate-finding review
    ai/              AIProvider abstraction (disabled/Ollama)
    auth.py, memberships.py, audit.py, crypto.py, webhooks.py, exports.py,
    rate_limit.py, user_accounts.py   — Milestone 7 additions
  api/v1/            the versioned JSON API, one router module per resource
  web/               the server-rendered Jinja2 + HTMX UI
    routes/          one module per nav section, plus auth.py, settings.py
    templates/, static/
```

**The service layer is the single source of truth for business logic.** Both
`api/v1/*.py` and `web/routes/*.py` call into `services/*` for anything that
mutates data (`apply_exposure_update`, `handle_upload`, `resolve_merge`,
`accept_candidate`, `add_member`, `create_webhook`, ...) rather than each
implementing its own copy — this is what guarantees the audit trail, the
differential engine, and the authorization model behave identically whether
a change came from a `curl` request or a click in the browser.

## Request lifecycle (authenticated, workspace-scoped route)

1. `MaxBodySizeMiddleware` / `SecurityHeadersMiddleware` (ASGI-level, every
   request).
2. `get_current_user` (API) / `get_current_user` (web): resolve the
   `threnda_session` cookie to a live `UserSession` → `User`, or reject
   (`401` for the API, a redirect to `/login` for the web UI).
3. `get_workspace_membership` / `get_current_membership`: resolve the
   caller's `WorkspaceMembership` for the `workspace_id` in play, or `404`.
4. `require_analyst` / `require_admin` (only on routes that need more than
   read access): `403` if the membership's role isn't high enough.
5. The route handler, which calls into `services/*` and returns a Pydantic
   model (API) or renders a Jinja2 template (web).
6. The request-scoped SQLAlchemy session commits on success, rolls back on
   any exception (`threnda.app.db.session.iter_session`).

See [security.md](security.md) for the authorization/session/CSRF design in
full, and [data-model.md](data-model.md) for the schema this all sits on.

## Why SQLite by default, PostgreSQL-compatible

The schema deliberately avoids SQLite-only constructs (enums are
`VARCHAR` + `CHECK`, not native `ENUM`; timestamps use a custom
`UTCDateTime` type that normalizes both dialects to timezone-aware UTC) so
the same models and migrations work against either database without a
branch anywhere in application code. SQLite is the default because it needs
zero setup for local development and a single-user/small-team Community
deployment; the shipped `docker-compose.yml` uses PostgreSQL because a
containerized deployment already has to run a database process regardless,
and PostgreSQL handles concurrent writes from multiple browser tabs/API
clients more gracefully than SQLite's single-writer model.

## What this is not

- Not microservices, not Kubernetes, not a queue-based architecture — see
  CLAUDE.md, "Do not introduce without strong justification".
- Not horizontally scaled — one process, one database. Enterprise's HA/BYOC
  story is a separate, private codebase (CLAUDE.md, "Community / Enterprise
  boundary").
