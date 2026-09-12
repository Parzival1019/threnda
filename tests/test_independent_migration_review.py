"""Verify real schema upgrades preserve data, including rejected upgrades.

Data is seeded with raw SQL matching the ``INITIAL_REVISION`` schema
exactly, deliberately *not* via the live ORM model classes: those always
reflect the current models, which drift from any one frozen historical
migration as soon as a later migration adds a column (as Milestone 3's
did). Seeding through the live models would make this test start failing
every time the schema grows — not because upgrading a real legacy database
stopped working, but because the seeding helper itself no longer matches
the revision it claims to seed.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

INITIAL_REVISION = "1c704bf9919a"
TABLES = ("workspaces", "sources", "assets", "exposures", "import_runs", "findings")


def migrate(db_path: Path, revision: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "THRENDA_DATABASE_URL": f"sqlite:///{db_path}"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def seed_initial_database(db_path: Path) -> None:
    """Seed one workspace/source/asset/exposure/import_run/finding, using
    only the columns that exist as of ``INITIAL_REVISION``."""
    result = migrate(db_path, INITIAL_REVISION)
    assert result.returncode == 0, result.stderr

    now = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO workspaces (id, name, created_at) VALUES (1, ?, ?)",
            ("Synthetic preserved workspace", now),
        )
        connection.execute(
            "INSERT INTO sources (id, workspace_id, name, source_type, metadata, created_at) "
            "VALUES (1, 1, ?, 'json', '{}', ?)",
            ("Synthetic JSON", now),
        )
        connection.execute(
            "INSERT INTO assets "
            "(id, workspace_id, canonical_key, name, asset_type, environment, metadata, "
            "first_seen, last_seen) VALUES (1, 1, ?, ?, 'host', 'unknown', '{}', ?, ?)",
            ("host:preserved.example.test", "Synthetic preserved asset", now, now),
        )
        connection.execute(
            "INSERT INTO exposures "
            "(id, workspace_id, canonical_fingerprint, title, severity, risk_score, status, "
            "owner, acknowledged, first_seen, last_seen, created_at, updated_at) "
            "VALUES (1, 1, 'synthetic-preserved', ?, 'high', 0, 'open', ?, 0, ?, ?, ?, ?)",
            ("Synthetic preserved exposure", "synthetic-owner", now, now, now, now),
        )
        connection.execute(
            "INSERT INTO import_runs "
            "(id, workspace_id, source_id, filename, file_hash, started_at, status, "
            "total_records, successful_records, failed_records) "
            "VALUES (1, 1, 1, 'synthetic.json', ?, ?, 'completed', 1, 1, 0)",
            ("0" * 64, now),
        )
        connection.execute(
            "INSERT INTO findings "
            "(id, workspace_id, fingerprint, source_id, import_run_id, source_finding_id, "
            'title, severity, asset_id, exposure_id, "references", raw_data, '
            "first_seen, last_seen, created_at, updated_at) "
            "VALUES (1, 1, 'synthetic-preserved', 1, 1, 'synthetic-preserved', "
            "'Synthetic preserved finding', 'high', 1, 1, '[]', ?, ?, ?, ?, ?)",
            ('{"fixture": "synthetic-only"}', now, now, now, now),
        )
        connection.commit()


# Columns as of INITIAL_REVISION, i.e. exactly what seed_initial_database()
# writes. Deliberately not `SELECT *`: a later migration legitimately adding
# a column (as Milestone 3's did, for findings.resolved_at and five new
# import_runs counters) changes what `SELECT *` returns for every existing
# row without losing or corrupting a single original value — comparing the
# full row shape before/after would make this test fail on every future
# column-adding migration for no real reason. Comparing only the columns
# that existed *before* the upgrade is what "preserved" actually means here.
ORIGINAL_COLUMNS = {
    "workspaces": ("id", "name", "created_at"),
    "sources": ("id", "workspace_id", "name", "source_type", "metadata", "created_at"),
    "assets": (
        "id",
        "workspace_id",
        "canonical_key",
        "name",
        "asset_type",
        "environment",
        "metadata",
        "first_seen",
        "last_seen",
    ),
    "exposures": (
        "id",
        "workspace_id",
        "canonical_fingerprint",
        "title",
        "severity",
        "risk_score",
        "status",
        "owner",
        "acknowledged",
        "first_seen",
        "last_seen",
        "created_at",
        "updated_at",
    ),
    "import_runs": (
        "id",
        "workspace_id",
        "source_id",
        "filename",
        "file_hash",
        "started_at",
        "status",
        "total_records",
        "successful_records",
        "failed_records",
    ),
    "findings": (
        "id",
        "workspace_id",
        "fingerprint",
        "source_id",
        "import_run_id",
        "source_finding_id",
        "title",
        "severity",
        "asset_id",
        "exposure_id",
        "references",
        "raw_data",
        "first_seen",
        "last_seen",
        "created_at",
        "updated_at",
    ),
}


def snapshot(db_path: Path) -> dict[str, list[tuple[object, ...]]]:
    with sqlite3.connect(db_path) as connection:
        result: dict[str, list[tuple[object, ...]]] = {}
        for table in TABLES:
            column_list = ", ".join(f'"{column}"' for column in ORIGINAL_COLUMNS[table])
            result[table] = connection.execute(
                f"SELECT {column_list} FROM {table} ORDER BY id"  # noqa: S608
            ).fetchall()
        return result


def test_real_upgrade_preserves_all_linked_records(tmp_path: Path) -> None:
    db_path = tmp_path / "synthetic-valid-upgrade.db"
    seed_initial_database(db_path)
    before = snapshot(db_path)
    result = migrate(db_path, "head")
    assert result.returncode == 0, result.stderr
    assert snapshot(db_path) == before

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE findings SET severity='invalid-severity'")
        connection.rollback()

        # Columns Milestone 3 added get sane, honest defaults for a
        # pre-existing row — not fabricated real counts.
        assert connection.execute("SELECT resolved_at FROM findings WHERE id = 1").fetchone() == (
            None,
        )
        assert connection.execute(
            "SELECT new_findings, unchanged_findings, updated_findings, "
            "resolved_findings, reopened_findings FROM import_runs WHERE id = 1"
        ).fetchone() == (0, 0, 0, 0, 0)


@pytest.mark.parametrize("invalid_data", ["enum", "workspace"])
def test_invalid_legacy_data_is_rejected_without_loss(tmp_path: Path, invalid_data: str) -> None:
    db_path = tmp_path / f"synthetic-invalid-{invalid_data}.db"
    seed_initial_database(db_path)
    with sqlite3.connect(db_path) as connection:
        if invalid_data == "enum":
            connection.execute("UPDATE findings SET severity='invalid-severity'")
        else:
            connection.execute(
                "INSERT INTO workspaces (id, name, created_at) "
                "VALUES (2, 'Synthetic other workspace', '2026-01-01T00:00:00+00:00')"
            )
            connection.execute("UPDATE findings SET workspace_id=2")
        connection.commit()
    before = snapshot(db_path)
    result = migrate(db_path, "head")
    assert result.returncode != 0, "An invalid legacy database must not be silently accepted"
    assert snapshot(db_path) == before
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            INITIAL_REVISION,
        )
