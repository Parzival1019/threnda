# Deployment

This covers the Docker Compose deployment: bootstrap, TLS/reverse proxy,
backup/restore, and upgrades. For local Python development instead, see the
README's "Local development" section.

## Quick start

```bash
git clone <this repository>
cd threnda
cp .env.example .env
```

Edit `.env` and set, at minimum:

- `THRENDA_SECRET_KEY` — generate one: `python -c "import secrets; print(secrets.token_urlsafe(64))"`
- `POSTGRES_PASSWORD` — any strong password; Compose refuses to start without it
- `THRENDA_BOOTSTRAP_ADMIN_USERNAME` / `THRENDA_BOOTSTRAP_ADMIN_PASSWORD` —
  see "Bootstrapping the first admin" below

Then:

```bash
docker compose up -d
```

Compose builds the image, starts PostgreSQL, waits for it to report healthy,
runs `alembic upgrade head` inside the app container automatically, creates
the first admin account from the bootstrap variables above, and starts the
application. Open **http://localhost:8080** and log in.

No manual database setup is required — this is the "no manual database
configuration" target from `docs/community-v0.1-spec.md`.

## Bootstrapping the first admin

There is no self-registration page anywhere in Threnda. The first account is
created exactly once, by one of two equivalent paths:

- **Docker Compose** (recommended): set `THRENDA_BOOTSTRAP_ADMIN_USERNAME`
  and `THRENDA_BOOTSTRAP_ADMIN_PASSWORD` in `.env` before the first
  `docker compose up`. The container entrypoint runs `threnda bootstrap-admin`
  on every start; it creates the account only when the `users` table is
  empty and is a harmless no-op every time after that. You can safely blank
  those two variables out again once the account exists — nothing reads
  them after the first successful run.
- **Local/manual**: run `threnda bootstrap-admin` yourself against a
  migrated database. With a TTY attached and the environment variables
  unset, it prompts interactively (`getpass`, not echoed, with
  confirmation) instead.

Either way, bootstrap **refuses to run a second time** once any account
exists (exit code `2` — a deliberately distinct, benign outcome, since the
container entrypoint hits this on every restart after the first; a real
failure such as a rejected weak password or an unmigrated database exits
`1`) and **fails closed** if the database isn't migrated yet, or if
`THRENDA_ENV=production` and `THRENDA_SECRET_KEY` is missing, the
placeholder value, or too short (`threnda/app/core/startup_checks.py`).
Every account after the first is created by an existing admin on
**Settings → Members**.

## TLS / reverse proxy

Threnda's own container binds to `127.0.0.1:8080` on the host — it does not
terminate TLS itself. For anything beyond local access, put a reverse proxy
(Caddy, Nginx, Traefik) in front of it that:

1. Terminates TLS.
2. Proxies to `http://127.0.0.1:8080` (or joins the Compose network and
   proxies to the `app` service directly).
3. Passes through (or sets) `X-Forwarded-Proto: https` so the app can tell
   the connection is HTTPS — Threnda's cookie `Secure` flag logic
   (`threnda.app.web.cookies.cookie_secure`) currently keys off
   `THRENDA_ENV=production` and the request's own scheme; when a proxy
   terminates TLS, set `THRENDA_ENV=production` so `Secure` is applied even
   though the app itself only ever sees plain HTTP from the proxy.

A minimal Caddy example (`Caddyfile`):

```
threnda.example.org {
    reverse_proxy 127.0.0.1:8080
}
```

Caddy handles TLS certificate acquisition automatically. Never set
`THRENDA_ALLOW_INSECURE_COOKIES=true` behind a real reverse proxy — it's a
local-development-only override and Threnda refuses to start with it in
production.

## Backup and restore

Everything Threnda needs to reconstruct its state lives in the PostgreSQL
database (the `threnda-db-data` named volume). There is no separate
file-based store to also back up beyond that — uploaded documents are
extracted to text and stored in the database, not kept as files on disk.

**Backup** (while the stack is running):

```bash
docker compose exec db pg_dump -U threnda threnda | gzip > threnda-backup-$(date +%Y%m%d).sql.gz
```

**Restore** into a fresh instance:

```bash
docker compose up -d db          # start only the database
gunzip -c threnda-backup-20260101.sql.gz | docker compose exec -T db psql -U threnda threnda
docker compose up -d             # start the app; it will find the schema already migrated
```

If restoring into a database at an older schema version than the app image
expects, start the app anyway — the entrypoint's `alembic upgrade head` will
bring it forward automatically before serving traffic.

## Upgrades

```bash
git pull
docker compose build
docker compose up -d
```

The entrypoint always runs `alembic upgrade head` before starting the
application, so a schema migration that shipped between versions is applied
automatically. Take a backup first (see above) if the release notes call out
a migration with a downgrade caveat; every migration in this repository has
been verified with a real upgrade/downgrade/upgrade round trip (see
`docs/implementation-plan.md`), but a backup costs one command and nothing
else.

## Release checklist

Before tagging a Community release:

- [ ] `pytest`, `ruff check`, `ruff format --check`, `mypy threnda tests`,
      `pip check` all clean.
- [ ] `alembic upgrade head` → `alembic downgrade -1` → `alembic upgrade head`
      round trip verified against a fresh database.
- [ ] `docker compose build && docker compose up -d` from a clean checkout
      reaches a healthy `app` container and `GET /api/v1/health` returns
      `200`.
- [ ] A fresh bootstrap → login → upload demo CSV/SARIF/JSON → acknowledge
      an exposure → re-upload a changed export → see new/resolved/reopened
      → export CSV/JSON walkthrough performed by hand at least once.
- [ ] `docs/implementation-plan.md` reflects the milestone's actual,
      verified status — not aspirational status.
- [ ] No secret, real hostname, or non-synthetic data anywhere in the diff
      (`demo-data/` included).
