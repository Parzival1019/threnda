"""Regression guard for the README quickstart's `mkdir -p data` step.

A relative ``sqlite:///./data/threnda.db`` URL requires the parent
directory to exist first — SQLite will not create it. This is exactly the
failure a fresh clone hits without the quickstart's ``mkdir -p data``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from threnda.app.core.config import Settings
from threnda.app.db.session import create_engine_from_settings


def test_sqlite_connection_fails_when_parent_directory_is_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "threnda.db"
    settings = Settings(  # type: ignore[call-arg]
        database_url=f"sqlite:///{db_path}",
        secret_key="test-secret",
        _env_file=None,  # noqa: S106
    )
    engine = create_engine_from_settings(settings)

    assert not db_path.parent.exists()
    with pytest.raises(OperationalError), engine.connect():
        pass
    engine.dispose()


def test_sqlite_connection_succeeds_once_parent_directory_exists(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "threnda.db"
    db_path.parent.mkdir(parents=True)
    settings = Settings(  # type: ignore[call-arg]
        database_url=f"sqlite:///{db_path}",
        secret_key="test-secret",
        _env_file=None,  # noqa: S106
    )
    engine = create_engine_from_settings(settings)

    with engine.connect():
        pass
    engine.dispose()
