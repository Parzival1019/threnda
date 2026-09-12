"""Migration coverage for 0f2f55660e01 (Milestone 4 risk-scoring fields).

``tests/test_migrations.py`` already proves the full chain (including this
revision) upgrades/downgrades cleanly from scratch; this file checks the
specific things that revision is responsible for: the new columns actually
appear/disappear at exactly this step, an existing (pre-Milestone-4) row
gets the documented default rather than an error, and the new CHECK
constraint is actually enforced by the database, not just documented.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

PRE_REVISION = "32832b96f2c3"
NOW = "2026-01-01T00:00:00+00:00"


def run_alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", *args],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "THRENDA_DATABASE_URL": f"sqlite:///{db_path}"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _seed_pre_migration_asset(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO workspaces (id, name, created_at) VALUES (1, 'w', ?)", (NOW,))
    connection.execute(
        "INSERT INTO assets "
        "(id, workspace_id, canonical_key, name, asset_type, environment, metadata, "
        "first_seen, last_seen) VALUES (1, 1, 'host:a', 'a', 'host', 'production', '{}', ?, ?)",
        (NOW, NOW),
    )
    connection.commit()


def test_columns_appear_only_after_this_revision(tmp_path: Path) -> None:
    db_path = tmp_path / "before.db"
    assert run_alembic(db_path, "upgrade", PRE_REVISION).returncode == 0
    assert "internet_facing" not in _columns(db_path, "assets")
    assert "business_criticality" not in _columns(db_path, "assets")
    assert "exploit_evidence" not in _columns(db_path, "findings")

    assert run_alembic(db_path, "upgrade", "head").returncode == 0
    assert "internet_facing" in _columns(db_path, "assets")
    assert "business_criticality" in _columns(db_path, "assets")
    assert "exploit_evidence" in _columns(db_path, "findings")


def test_existing_asset_gets_unknown_criticality_default(tmp_path: Path) -> None:
    """A non-empty `assets` table must survive adding the NOT NULL column —
    SQLite/PostgreSQL both reject that without a server_default."""
    db_path = tmp_path / "existing_row.db"
    assert run_alembic(db_path, "upgrade", PRE_REVISION).returncode == 0
    with sqlite3.connect(db_path) as connection:
        _seed_pre_migration_asset(connection)

    result = run_alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT business_criticality, internet_facing FROM assets WHERE id = 1"
        ).fetchone()
    assert row == ("unknown", None)


def test_business_criticality_check_constraint_is_enforced(tmp_path: Path) -> None:
    db_path = tmp_path / "constraint.db"
    assert run_alembic(db_path, "upgrade", "head").returncode == 0
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO workspaces (id, name, created_at) VALUES (1, 'w', ?)", (NOW,)
        )
        try:
            connection.execute(
                "INSERT INTO assets "
                "(id, workspace_id, canonical_key, name, asset_type, environment, "
                "business_criticality, metadata, first_seen, last_seen) "
                "VALUES "
                "(1, 1, 'host:a', 'a', 'host', 'production', 'not-a-real-value', '{}', ?, ?)",
                (NOW, NOW),
            )
        except sqlite3.IntegrityError as exc:
            assert "ck_assets_assetcriticality" in str(exc) or "CHECK" in str(exc)
        else:
            raise AssertionError("expected the CHECK constraint to reject an invalid value")


def test_downgrade_removes_exactly_these_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "downgrade.db"
    assert run_alembic(db_path, "upgrade", "head").returncode == 0
    assert run_alembic(db_path, "downgrade", PRE_REVISION).returncode == 0
    assert "internet_facing" not in _columns(db_path, "assets")
    assert "business_criticality" not in _columns(db_path, "assets")
    assert "exploit_evidence" not in _columns(db_path, "findings")

    # And re-upgrading afterward must still work (proves downgrade left a
    # genuinely valid, re-migratable schema behind — not just "no crash").
    assert run_alembic(db_path, "upgrade", "head").returncode == 0
    assert "business_criticality" in _columns(db_path, "assets")
