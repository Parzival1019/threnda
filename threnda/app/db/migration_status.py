"""Compare an application database against the checked-in Alembic migrations.

Used by the health endpoint (``threnda.app.api.v1.health``) to distinguish a
genuinely ready database from one that is uninitialized (no migrations ever
applied) or outdated (behind the migrations checked into this copy of the
codebase) — a distinction ``SELECT 1`` cannot make, since it succeeds against
an empty SQLite file just as readily as a fully migrated one.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

# threnda/app/db/migration_status.py -> repo root is three parents up.
REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = REPO_ROOT / "migrations"


def get_expected_head_revision() -> str | None:
    """Return the Alembic head revision from the migrations on disk.

    Returns ``None`` if the migrations directory isn't available in this
    deployment (e.g. an unusual packaging layout) rather than raising —
    callers treat that as "can't tell", not as a database problem.
    """
    if not MIGRATIONS_DIR.is_dir():
        return None
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    script = ScriptDirectory.from_config(config)
    return script.get_current_head()


def get_database_status(db: Session) -> str:
    """Classify the database's migration state as ``ok``, ``uninitialized`` or ``outdated``.

    Never raises and never includes raw driver/exception text in its return
    value — callers (the health endpoint) surface this directly to API
    clients, so it must not leak database paths, driver names or internal
    error details.
    """
    try:
        applied_revision = db.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
    except SQLAlchemyError:
        return "uninitialized"

    if applied_revision is None:
        return "uninitialized"

    expected_revision = get_expected_head_revision()
    if expected_revision is None:
        # Can't determine the expected head in this deployment; a readable
        # alembic_version row is the best signal available, so treat it as ok
        # rather than failing health over an unrelated packaging issue.
        return "ok"

    if applied_revision != expected_revision:
        return "outdated"

    return "ok"
