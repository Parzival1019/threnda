"""Shared pytest fixtures.

Every fixture here uses a temporary, per-test SQLite file (via pytest's
``tmp_path``) — no test ever touches a developer's real ``data/threnda.db``.
Schema creation uses ``Base.metadata.create_all`` for speed; migration
correctness itself is verified separately in ``tests/test_migrations.py``.

Milestone 7 (authentication/RBAC): the ``client`` fixture is a real,
authenticated session — logged in as ``admin_user`` — because every
workspace-scoped route now requires one. ``admin_user`` itself is not a
member of any workspace; a workspace created *through* ``client``
(``POST /api/v1/workspaces`` or the equivalent web form) automatically
grants the creator admin membership, so most existing tests need no
change at all. Tests that seed a ``Workspace`` directly via ``db_session``
(bypassing the API) must grant membership explicitly with
:func:`grant_membership` — see that function's docstring.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import threnda.app.models  # noqa: F401 - ensure all mappers are registered
from threnda.app.core.config import AIProvider, Settings
from threnda.app.db.base import Base
from threnda.app.db.session import create_engine_from_settings
from threnda.app.main import create_app
from threnda.app.models.enums import WorkspaceRole
from threnda.app.models.user import User
from threnda.app.models.workspace import Workspace
from threnda.app.services.memberships import add_member
from threnda.app.services.rate_limit import login_ip_rate_limiter
from threnda.app.services.user_accounts import create_user

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _reset_login_rate_limiter() -> Iterator[None]:
    """Isolate the module-level login rate limiter between tests.

    ``login_ip_rate_limiter`` is a process-wide singleton (see
    ``threnda.app.services.rate_limit``), and every ``TestClient`` request
    reports the same source "IP" — without this, a test that intentionally
    trips the limiter would leave every later test's login attempt (e.g.
    the ``client`` fixture's own login) rate-limited too.
    """
    login_ip_rate_limiter.clear()
    yield
    login_ip_rate_limiter.clear()


# Meets the baseline password policy (12+ chars, 2+ character classes) —
# see threnda.app.services.auth.validate_password_strength.
DEFAULT_TEST_PASSWORD = "Sup3r-Secret-Test-Passw0rd!"  # noqa: S105 - test fixture only


@dataclass(frozen=True)
class TestUser:
    """A created account's id/username/plaintext password, for logging in
    as it again (or as a *different* user, for cross-account tests) after
    the ORM object itself may have gone out of scope."""

    # Tells pytest not to try to collect this as a test class, despite the
    # `Test`-prefixed name — it's a plain data holder, not a test.
    __test__ = False

    id: int
    username: str
    password: str


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    db_path = tmp_path / "test.db"
    # `_env_file=None` makes this hermetic against whatever real `.env` a
    # developer happens to have checked out locally (e.g. one used for a
    # manual Docker Compose smoke test) — tests must never inherit
    # THRENDA_ENV=production or any other stray setting from it.
    return Settings(  # type: ignore[call-arg]
        database_url=f"sqlite:///{db_path}",
        secret_key="test-secret",  # noqa: S106
        ai_provider=AIProvider.DISABLED,
        _env_file=None,
    )


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


def _create_user(app: FastAPI, username: str, password: str) -> TestUser:
    session = app.state.sessionmaker()
    try:
        user = create_user(session, username, password, actor=None)
        # Both fixture accounts are consultancy staff allowed to create
        # their own projects; tests for the non-org-admin refusal create
        # their own account explicitly.
        user.is_org_admin = True
        session.commit()
        return TestUser(id=user.id, username=user.username, password=password)
    finally:
        session.close()


@pytest.fixture
def admin_user(app: FastAPI) -> TestUser:
    """A local account with no workspace membership yet.

    ``client`` logs in as this user. Most tests never need to think about
    it directly — it exists so ``grant_membership`` and multi-user tests
    (permissions matrix, IDOR) have a stable identity to grant/withhold
    access for.
    """
    return _create_user(app, "admin", DEFAULT_TEST_PASSWORD)


@pytest.fixture
def other_user(app: FastAPI) -> TestUser:
    """A second, independent account — never a member of anything by
    default. Use this (with ``other_client``) for cross-workspace/IDOR
    tests: a resource ``client``/``admin_user`` can see must be invisible
    to this account unless a test explicitly grants it membership.
    """
    return _create_user(app, "other", DEFAULT_TEST_PASSWORD)


def _login(test_client: TestClient, user: TestUser) -> None:
    response = test_client.post(
        "/api/v1/auth/login", json={"username": user.username, "password": user.password}
    )
    assert response.status_code == 200, response.text


@pytest.fixture
def client(app: FastAPI, admin_user: TestUser) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        _login(test_client, admin_user)
        yield test_client


@pytest.fixture
def other_client(app: FastAPI, other_user: TestUser) -> Iterator[TestClient]:
    """A second authenticated client, logged in as ``other_user``."""
    with TestClient(app) as test_client:
        _login(test_client, other_user)
        yield test_client


@pytest.fixture
def anonymous_client(app: FastAPI) -> Iterator[TestClient]:
    """A client with no session cookie at all — for auth-required assertions."""
    with TestClient(app) as test_client:
        yield test_client


def grant_membership(
    db_session: Session,
    workspace: Workspace,
    user: TestUser | User,
    role: WorkspaceRole = WorkspaceRole.ADMIN,
) -> None:
    """Grant ``user`` a role in ``workspace``, committed immediately.

    Needed only for a test that creates a ``Workspace`` directly via
    ``db_session`` (bypassing ``POST /api/v1/workspaces``, which grants
    admin membership to its caller automatically) and then expects an
    authenticated ``client``/``other_client`` to reach it. Uses its own
    short-lived flush + commit so the membership is visible to the
    session ``client`` makes its HTTP requests through, regardless of
    which session object created the workspace.
    """
    user_id = user.id
    resolved_user = db_session.get(User, user_id)
    assert resolved_user is not None
    add_member(db_session, workspace, resolved_user, role, actor=resolved_user)
    db_session.commit()


@pytest.fixture
def migrated_settings(tmp_path: Path) -> Settings:
    """Settings for a database migrated with the real Alembic CLI, not ``create_all``.

    Used by tests that specifically care about migration state (health
    endpoint tests) — everything else uses the faster ``initialized_settings``.
    """
    db_path = tmp_path / "migrated.db"
    env = dict(os.environ)
    env["THRENDA_DATABASE_URL"] = f"sqlite:///{db_path}"
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return Settings(  # type: ignore[call-arg]
        database_url=f"sqlite:///{db_path}",
        secret_key="test-secret",  # noqa: S106
        ai_provider=AIProvider.DISABLED,
        _env_file=None,
    )


@pytest.fixture
def migrated_client(migrated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings=migrated_settings)) as test_client:
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
