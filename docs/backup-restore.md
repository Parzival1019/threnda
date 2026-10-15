# Backup and restore

Everything — findings, history, comments, retests, accounts, **and evidence
attachments** — lives in the database. Back up the database and you have
everything. Treat backups as sensitive client data: encrypt them at rest and
restrict who can read them.

## Docker Compose (PostgreSQL)

Backup while running:

```bash
docker compose exec -T db pg_dump -U threnda -Fc threnda > threnda-$(date +%Y%m%d-%H%M).dump
```

Restore (into the same or a fresh deployment):

```bash
docker compose stop app
docker compose exec -T db dropdb -U threnda --if-exists threnda
docker compose exec -T db createdb -U threnda threnda
docker compose exec -T db pg_restore -U threnda -d threnda --no-owner < threnda-20261015-0900.dump
docker compose start app      # the entrypoint runs `alembic upgrade head` first
```

Verify: log in, open **All projects**, open one finding with an attachment
and download it.

> Status: the PostgreSQL commands above follow the standard `pg_dump` /
> `pg_restore` workflow but were **not executed** during the pilot-readiness
> work (no Docker daemon was available on the build machine). Run one
> backup → restore drill on your own server before the first pilot.

## SQLite (single server without Compose, Desktop, demo instance)

```bash
threnda backup /secure/place/threnda-20261015.db     # consistent online copy, mode 0600
# restore — stop Threnda first:
threnda restore /secure/place/threnda-20261015.db    # asks for confirmation (--yes to skip)
alembic upgrade head
```

`threnda restore` checks that the file is an intact Threnda database
(SQLite integrity check + schema present) and keeps the replaced database as
`<name>.before-restore`. `threnda backup` never overwrites an existing file.

Tested: `tests/test_pilot_operations.py::test_cli_backup_and_restore_round_trip`
creates the demo dataset, backs it up, corrupts the live database, restores,
and then logs in and downloads an attachment from the restored data.

## Schedule

Suggested starting point for a pilot: a daily backup kept for 14 days, plus
one before every upgrade. Copy backups off the server.
