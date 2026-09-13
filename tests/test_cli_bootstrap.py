"""``threnda bootstrap-admin`` — the one-time first-admin CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from threnda.app.cli import bootstrap_admin
from threnda.app.core.config import Settings
from threnda.app.db.session import create_engine_from_settings
from threnda.app.models.enums import WorkspaceRole
from threnda.app.models.user import User
from threnda.app.models.workspace import Workspace
from threnda.app.models.workspace_membership import WorkspaceMembership

REPO_ROOT = Path(__file__).resolve().parents[1]


def _migrate(db_path: Path) -> None:
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


def _set_bootstrap_env(
    monkeypatch: pytest.MonkeyPatch, db_path: Path, *, username: str, password: str, **extra: str
) -> None:
    # Explicit THRENDA_ENV/THRENDA_SECRET_KEY here (rather than leaving
    # them unset) so this is hermetic against whatever real `.env` a
    # developer happens to have checked out — e.g. one left over from a
    # manual Docker Compose smoke test, which sets THRENDA_ENV=production
    # and would otherwise make every one of these tests exercise the
    # fail-closed production path by accident.
    monkeypatch.setenv("THRENDA_ENV", "development")
    monkeypatch.setenv("THRENDA_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("THRENDA_SECRET_KEY", "cli-test-secret-at-least-32-characters-long")
    monkeypatch.setenv("THRENDA_BOOTSTRAP_ADMIN_USERNAME", username)
    monkeypatch.setenv("THRENDA_BOOTSTRAP_ADMIN_PASSWORD", password)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    # get_settings() is lru_cache'd — bypass it for a clean read of the env
    # vars this test just set.
    from threnda.app.core.config import get_settings

    get_settings.cache_clear()


def _read_db(db_path: Path) -> sessionmaker[Session]:
    settings = Settings(database_url=f"sqlite:///{db_path}", secret_key="cli-test-secret")
    engine = create_engine_from_settings(settings)
    return sessionmaker(bind=engine)


def test_bootstrap_creates_the_first_admin_and_a_default_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bootstrap.db"
    _migrate(db_path)
    _set_bootstrap_env(monkeypatch, db_path, username="admin", password="Bootstrap-Pass-1234!")

    exit_code = bootstrap_admin()
    assert exit_code == 0

    session = _read_db(db_path)()
    user = session.execute(select(User).where(User.username == "admin")).scalar_one()
    membership = session.execute(
        select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)
    ).scalar_one()
    assert membership.role == WorkspaceRole.ADMIN
    workspace = session.get(Workspace, membership.workspace_id)
    assert workspace is not None and workspace.name == "Default"
    session.close()


def test_bootstrap_refuses_to_run_a_second_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bootstrap.db"
    _migrate(db_path)
    _set_bootstrap_env(monkeypatch, db_path, username="admin", password="Bootstrap-Pass-1234!")
    assert bootstrap_admin() == 0

    monkeypatch.setenv("THRENDA_BOOTSTRAP_ADMIN_USERNAME", "second-admin")
    exit_code = bootstrap_admin()
    # 2, not 1: a deliberately distinct, benign "already bootstrapped"
    # outcome — see threnda/app/cli.py::bootstrap_admin.
    assert exit_code == 2

    session = _read_db(db_path)()
    assert (
        session.execute(select(User).where(User.username == "second-admin")).scalar_one_or_none()
        is None
    )
    session.close()


def test_bootstrap_refuses_against_an_unmigrated_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "unmigrated.db"  # never migrated
    _set_bootstrap_env(monkeypatch, db_path, username="admin", password="Bootstrap-Pass-1234!")
    assert bootstrap_admin() == 1


def test_bootstrap_rejects_a_weak_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "bootstrap.db"
    _migrate(db_path)
    _set_bootstrap_env(monkeypatch, db_path, username="admin", password="short")
    assert bootstrap_admin() == 1

    session = _read_db(db_path)()
    assert session.execute(select(User)).scalar_one_or_none() is None
    session.close()


def test_bootstrap_uses_an_existing_workspace_if_one_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bootstrap.db"
    _migrate(db_path)
    session = _read_db(db_path)()
    existing = Workspace(name="Pre-existing")
    session.add(existing)
    session.commit()
    existing_id = existing.id
    session.close()

    _set_bootstrap_env(monkeypatch, db_path, username="admin", password="Bootstrap-Pass-1234!")
    assert bootstrap_admin() == 0

    session = _read_db(db_path)()
    membership = session.execute(select(WorkspaceMembership)).scalar_one()
    assert membership.workspace_id == existing_id
    session.close()


def test_bootstrap_fails_closed_in_production_with_a_weak_secret_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bootstrap.db"
    _migrate(db_path)
    _set_bootstrap_env(
        monkeypatch,
        db_path,
        username="admin",
        password="Bootstrap-Pass-1234!",
        THRENDA_ENV="production",
    )
    monkeypatch.setenv("THRENDA_SECRET_KEY", "change-me")
    from threnda.app.core.config import get_settings

    get_settings.cache_clear()

    assert bootstrap_admin() == 1
    session = _read_db(db_path)()
    assert session.execute(select(User)).scalar_one_or_none() is None
    session.close()


def test_non_interactive_with_no_env_vars_fails_without_inventing_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bootstrap.db"
    _migrate(db_path)
    monkeypatch.setenv("THRENDA_ENV", "development")
    monkeypatch.setenv("THRENDA_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("THRENDA_SECRET_KEY", "cli-test-secret-at-least-32-characters-long")
    monkeypatch.delenv("THRENDA_BOOTSTRAP_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("THRENDA_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    from threnda.app.core.config import get_settings

    get_settings.cache_clear()

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit):
        bootstrap_admin()

    session = _read_db(db_path)()
    assert session.execute(select(User)).scalar_one_or_none() is None
    session.close()
