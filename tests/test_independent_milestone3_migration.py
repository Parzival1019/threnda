"""A Milestone 2 database must enter the Milestone 3 Exposure queue."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

MILESTONE_2_REVISION = "f6319dd94ee1"


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


def test_milestone2_unlinked_finding_is_backfilled_into_queue(tmp_path: Path) -> None:
    db_path = tmp_path / "synthetic-milestone2.db"
    result = migrate(db_path, MILESTONE_2_REVISION)
    assert result.returncode == 0, result.stderr
    now = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO workspaces (id, name, created_at) VALUES (1, ?, ?)",
            ("Synthetic legacy workspace", now),
        )
        connection.execute(
            "INSERT INTO sources (id, workspace_id, name, source_type, metadata, created_at) "
            "VALUES (1, 1, ?, 'json', '{}', ?)",
            ("Synthetic legacy source", now),
        )
        connection.execute(
            "INSERT INTO import_runs "
            "(id, workspace_id, source_id, filename, file_hash, started_at, status, "
            "total_records, successful_records, failed_records) "
            "VALUES (1, 1, 1, 'legacy.json', ?, ?, 'completed', 1, 1, 0)",
            ("0" * 64, now),
        )
        connection.execute(
            "INSERT INTO findings "
            "(id, workspace_id, fingerprint, source_id, import_run_id, source_finding_id, "
            'title, severity, exposure_id, "references", raw_data, first_seen, last_seen, '
            "created_at, updated_at) VALUES "
            "(1, 1, 'synthetic-legacy-fingerprint', 1, 1, 'legacy-1', "
            "'Synthetic legacy finding', 'high', NULL, '[]', '{}', ?, ?, ?, ?)",
            (now, now, now, now),
        )
        connection.commit()

    result = migrate(db_path, "head")
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(db_path) as connection:
        exposure_id = connection.execute("SELECT exposure_id FROM findings WHERE id = 1").fetchone()
        pending = connection.execute(
            "SELECT COUNT(*) FROM possible_duplicates WHERE finding_id = 1 AND status = 'pending'"
        ).fetchone()
        assert exposure_id is not None and pending is not None
        assert exposure_id[0] is not None or pending[0] == 1
