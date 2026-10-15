# Demo instance

A separate, resettable Threnda instance with fictional data only — for
sales demos and for prospects to click through. It never shares a database
with real client data.

## Start it

Docker (recommended):

```bash
docker compose -f docker-compose.demo.yml -p threnda-demo up -d --build
open http://localhost:8090
```

Without Docker (Python 3.12+, from the repository root):

```bash
python -m venv .venv && source .venv/bin/activate && pip install -e .
export THRENDA_DATABASE_URL=sqlite:///./data/demo.db THRENDA_DEMO_MODE=true \
       THRENDA_SECRET_KEY=demo-only-secret THRENDA_AI_PROVIDER=disabled
mkdir -p data && alembic upgrade head
threnda demo reset --yes
uvicorn threnda.app.main:app --host 127.0.0.1 --port 8090
```

## Accounts

All accounts use the demo password printed by `threnda demo reset`
(default `Demo-Threnda-2026!`, override with `THRENDA_DEMO_PASSWORD` — do
that if the demo is reachable by anyone else).

| Username | Role | Use |
| --- | --- | --- |
| `demo-admin` | Consultancy admin | presenter |
| `demo-pentester` | Pentester on both projects | retest step |
| `kestrel-client` | Client user, Kestrel Logistics (fictional) | client portal with history |
| `bellwether-client` | Client user, Bellwether Dental (fictional) | isolation demo |
| `harbor-client` | Client user of Harbor Analytics (fictional), no project yet | live demo |

## Data

Two fictional clients with projects in every lifecycle state (open,
overdue, in progress, fixed — awaiting retest, failed retest, verified
closed, risk accepted, internal vs client-visible comments, evidence), and a
third client, *Harbor Analytics*, with no project — created live in the
demo. Demo files for the live part are in [`demo-data/pilot/`](../demo-data/pilot/README.md).
All hosts are `.example` domains or RFC 5737 documentation addresses.

## Reset

- In the app: log in as `demo-admin` → **Members & settings** → **Reset demo data**.
- CLI: `threnda demo reset --yes` (in Docker: `docker compose -f docker-compose.demo.yml -p threnda-demo exec app threnda demo reset --yes`).

## Separation safeguards

1. Reset/seed only run with `THRENDA_DEMO_MODE=true`; the in-app button only
   exists in demo mode and only for consultancy admins.
2. Reset deletes data only from a database that carries the demo marker
   (`instance_meta.instance_kind = demo`) written by the demo seed; seeding
   refuses any database that already has accounts. A real database never has
   the marker, so even a wrongly enabled demo mode cannot wipe it.
3. The demo Compose file uses its own project name, port (8090), volume and
   SQLite database, and a yellow "DEMO — fictional" banner is shown on every
   page.

Tests: `tests/test_pilot_operations.py` (refusal on a real database, seed,
reset, web reset only in demo mode, CLI refusal without demo mode).
