"""Migration tests.

These run the real Alembic CLI as a subprocess against a temporary SQLite
file, independent of ``Base.metadata.create_all`` — the rest of the test
suite persists via ``create_all`` for speed, so this file is what actually
proves the checked-in migration works.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TABLES = {
    "workspaces",
    "sources",
    "import_runs",
    "assets",
    "findings",
    "exposures",
    "exposure_history",
    "possible_duplicates",
    "documents",
    "candidate_findings",
    "alembic_version",
}


def _run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["THRENDA_DATABASE_URL"] = database_url
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def test_upgrade_head_creates_expected_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "migration_upgrade.db"
    _run_alembic("upgrade", "head", database_url=f"sqlite:///{db_path}")

    assert db_path.exists()
    assert _tables(db_path) == EXPECTED_TABLES


def test_upgrade_then_downgrade_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "migration_roundtrip.db"
    database_url = f"sqlite:///{db_path}"

    _run_alembic("upgrade", "head", database_url=database_url)
    assert "workspaces" in _tables(db_path)

    _run_alembic("downgrade", "base", database_url=database_url)
    assert _tables(db_path) == {"alembic_version"}


def test_migrated_database_accepts_a_row(tmp_path: Path) -> None:
    """A schema-only check isn't enough — verify inserts against the real migration."""
    db_path = tmp_path / "migration_insert.db"
    _run_alembic("upgrade", "head", database_url=f"sqlite:///{db_path}")

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO workspaces (id, name, created_at) "
            "VALUES (1, 'demo', '2026-01-01T00:00:00+00:00')"
        )
        connection.commit()
        (count,) = connection.execute("SELECT COUNT(*) FROM workspaces").fetchone()
    assert count == 1
