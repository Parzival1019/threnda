"""Community Desktop paths, Keychain secret handling and first-run onboarding."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from webhelpers import extract_csrf

from threnda.app.core.config import Settings
from threnda.app.desktop.launcher import (
    desktop_paths,
    desktop_settings,
    get_or_create_instance_secret,
    migrate_desktop_database,
    prepare_desktop_paths,
)
from threnda.app.main import create_app
from threnda.app.models.enums import WorkspaceRole
from threnda.app.models.user import User
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.services.auth import validate_session
from threnda.app.web.resources import web_resource_dir


class MemoryCredentialStore:
    def __init__(self) -> None:
        self.value: str | None = None
        self.writes = 0

    def get_password(self, service: str, username: str) -> str | None:
        assert service == "com.threnda.community.desktop"
        assert username == "instance-secret"
        return self.value

    def set_password(self, service: str, username: str, password: str) -> None:
        assert service == "com.threnda.community.desktop"
        assert username == "instance-secret"
        self.value = password
        self.writes += 1


def test_desktop_paths_live_in_application_support_and_are_private(tmp_path: Path) -> None:
    paths = desktop_paths(tmp_path)
    prepare_desktop_paths(paths)

    assert paths.database == tmp_path / "Library/Application Support/Threnda/threnda.db"
    assert paths.ai == tmp_path / "Library/Application Support/Threnda/ai"
    assert paths.root.stat().st_mode & 0o777 == 0o700
    assert paths.ai.stat().st_mode & 0o777 == 0o700


def test_desktop_data_directory_can_be_overridden_for_isolated_smoke_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated = tmp_path / "isolated-desktop"
    monkeypatch.setenv("THRENDA_DESKTOP_DATA_DIR", str(isolated))

    assert desktop_paths().root == isolated


def test_frozen_app_resolves_explicit_web_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled_templates = tmp_path / "templates"
    bundled_templates.mkdir()
    monkeypatch.setenv("RESOURCEPATH", str(tmp_path))

    assert web_resource_dir("templates") == bundled_templates


def test_instance_secret_is_generated_once_and_reused() -> None:
    store = MemoryCredentialStore()

    first = get_or_create_instance_secret(store)
    second = get_or_create_instance_secret(store)

    assert first == second
    assert len(first) >= 32
    assert store.writes == 1


def test_desktop_migrates_a_fresh_sqlite_database(tmp_path: Path) -> None:
    paths = desktop_paths(tmp_path)
    prepare_desktop_paths(paths)
    settings = desktop_settings(paths, "desktop-test-secret-at-least-32-characters")

    migrate_desktop_database(settings)

    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_desktop_routes_do_not_exist_in_server_mode(initialized_settings: Settings) -> None:
    with TestClient(create_app(initialized_settings)) as client:
        assert client.get("/desktop/start?token=anything").status_code == 404


def test_desktop_first_run_creates_owner_workspace_and_session(
    initialized_settings: Settings,
) -> None:
    app = create_app(initialized_settings, desktop_setup_token="one-time-desktop-token")
    with TestClient(app) as client:
        invalid = client.get("/desktop/start?token=wrong", follow_redirects=False)
        assert invalid.status_code == 404

        start = client.get("/desktop/start?token=one-time-desktop-token", follow_redirects=False)
        assert start.status_code == 303
        assert start.headers["location"] == "/desktop/setup"

        setup = client.get("/desktop/setup")
        assert setup.status_code == 200
        assert "Make this Threnda yours" in setup.text
        csrf = extract_csrf(setup.text)

        mismatch = client.post(
            "/desktop/setup",
            data={
                "csrf_token": csrf,
                "username": "owner",
                "password": "Desktop-Owner-Passw0rd!",
                "password_confirm": "Different-Owner-Passw0rd!",
            },
        )
        assert mismatch.status_code == 400
        assert "Passwords did not match" in mismatch.text

        csrf = extract_csrf(mismatch.text)
        created = client.post(
            "/desktop/setup",
            data={
                "csrf_token": csrf,
                "username": "owner",
                "password": "Desktop-Owner-Passw0rd!",
                "password_confirm": "Desktop-Owner-Passw0rd!",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        assert created.headers["location"] == "/"
        session_token = created.cookies.get("threnda_session")
        assert session_token

    db = app.state.sessionmaker()
    try:
        owner = db.execute(select(User).where(User.username == "owner")).scalar_one()
        membership = db.execute(
            select(WorkspaceMembership).where(WorkspaceMembership.user_id == owner.id)
        ).scalar_one()
        assert membership.role is WorkspaceRole.ADMIN
        assert validate_session(db, session_token) is not None
    finally:
        db.close()

    with TestClient(app) as client:
        assert client.get("/desktop/setup").status_code == 404
