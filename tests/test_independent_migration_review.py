"""Verify real schema upgrades preserve data, including rejected upgrades."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from threnda.app.core.config import Settings
from threnda.app.db.session import create_engine_from_settings
from threnda.app.models import Asset, Exposure, Finding, ImportRun, Source, Workspace
from threnda.app.models.enums import SeverityLevel, SourceType

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
    result = migrate(db_path, INITIAL_REVISION)
    assert result.returncode == 0, result.stderr
    engine = create_engine_from_settings(Settings(database_url=f"sqlite:///{db_path}"))
    with Session(engine) as db:
        workspace = Workspace(name="Synthetic preserved workspace")
        db.add(workspace)
        db.flush()
        source = Source(
            workspace_id=workspace.id, name="Synthetic JSON", source_type=SourceType.JSON
        )
        asset = Asset(
            workspace_id=workspace.id,
            canonical_key="host:preserved.example.test",
            name="Synthetic preserved asset",
        )
        exposure = Exposure(
            workspace_id=workspace.id,
            canonical_fingerprint="synthetic-preserved",
            title="Synthetic preserved exposure",
            severity=SeverityLevel.HIGH,
            owner="synthetic-owner",
        )
        db.add_all([source, asset, exposure])
        db.flush()
        run = ImportRun(
            workspace_id=workspace.id,
            source_id=source.id,
            filename="synthetic.json",
            file_hash="0" * 64,
        )
        db.add(run)
        db.flush()
        db.add(
            Finding(
                workspace_id=workspace.id,
                source_id=source.id,
                import_run_id=run.id,
                asset_id=asset.id,
                exposure_id=exposure.id,
                fingerprint="synthetic-preserved",
                source_finding_id="synthetic-preserved",
                title="Synthetic preserved finding",
                severity=SeverityLevel.HIGH,
                raw_data={"fixture": "synthetic-only"},
            )
        )
        db.commit()
    engine.dispose()


def snapshot(db_path: Path) -> dict[str, list[tuple[object, ...]]]:
    with sqlite3.connect(db_path) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()  # noqa: S608
            for table in TABLES
        }


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
