"""Shared pytest fixtures.

Every fixture here uses a temporary, per-test SQLite file (via pytest's
``tmp_path``) — no test ever touches a developer's real ``data/threnda.db``.
Schema creation uses ``Base.metadata.create_all`` for speed; migration
correctness itself is verified separately in ``tests/test_migrations.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import threnda.app.models  # noqa: F401 - ensure all mappers are registered
from threnda.app.core.config import Settings
from threnda.app.db.base import Base
from threnda.app.db.session import create_engine_from_settings
from threnda.app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    db_path = tmp_path / "test.db"
    return Settings(database_url=f"sqlite:///{db_path}", secret_key="test-secret")  # noqa: S106


@pytest.fixture
def initialized_settings(settings: Settings) -> Settings:
    """Settings for a database that already has the full schema applied."""
    engine = create_engine_from_settings(settings)
    Base.metadata.create_all(engine)
    engine.dispose()
    return settings


@pytest.fixture
def app(initialized_settings: Settings) -> FastAPI:
    return create_app(settings=initialized_settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db_session(app: FastAPI) -> Iterator[Session]:
    """A session bound to the same database the ``app``/``client`` fixtures use.

    Committed at the end so data seeded through this fixture is visible to
    API calls made through ``client`` in the same test.
    """
    session = app.state.sessionmaker()
    try:
        yield session
        session.commit()
    finally:
        session.close()
