#!/bin/sh
# Container entrypoint: apply migrations, optionally bootstrap the first
# admin, then run whatever CMD was given (normally uvicorn).
#
# Idempotent by design so `docker compose up` and every subsequent restart
# run the exact same steps safely:
#   - `alembic upgrade head` is a no-op once the schema is current.
#   - `threnda bootstrap-admin` refuses (a normal, harmless exit) once any
#     account exists — see threnda/app/cli.py. Its failure is intentionally
#     never fatal to the container: swallowing it means a missing/weak
#     bootstrap password shows up in `docker compose logs` rather than
#     crash-looping the container.
set -eu

echo "threnda-entrypoint: applying migrations..."
alembic upgrade head

if [ -n "${THRENDA_BOOTSTRAP_ADMIN_USERNAME:-}" ] && [ -n "${THRENDA_BOOTSTRAP_ADMIN_PASSWORD:-}" ]; then
    echo "threnda-entrypoint: running one-time admin bootstrap..."
    threnda bootstrap-admin || echo "threnda-entrypoint: bootstrap-admin did not create an account (see above) — continuing."
fi

if [ "${THRENDA_DEMO_MODE:-false}" = "true" ]; then
    echo "threnda-entrypoint: demo mode — loading fictional demo data if the database is empty..."
    threnda demo reset --if-empty
fi

echo "threnda-entrypoint: starting application..."
exec "$@"
