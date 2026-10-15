"""Invitations, demo-mode safety, log redaction and SQLite backup/restore."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import REPO_ROOT, TestUser
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from webhelpers import extract_csrf

from threnda.app.core.config import AIProvider, Settings
from threnda.app.core.log_redaction import SecretPathRedactingFilter
from threnda.app.main import create_app
from threnda.app.models.audit_log import AuditLogEntry
from threnda.app.models.enums import AuditEventType
from threnda.app.models.mixins import utcnow
from threnda.app.models.user import User
from threnda.app.models.user_invitation import UserInvitation
from threnda.app.services.demo import (
    DEMO_ADMIN,
    DemoResetRefusedError,
    demo_password,
    is_demo_database,
    reset_demo_data,
)


@pytest.fixture
def project(client: TestClient) -> int:
    created = client.post("/api/v1/clients", json={"name": "Eta (fictional)"}).json()
    return int(
        client.post(f"/api/v1/clients/{created['id']}/projects", json={"name": "Eta"}).json()["id"]
    )


def _invite(client: TestClient, project: int, username: str, role: str = "client") -> str:
    response = client.post(
        "/api/v1/invitations",
        params={"workspace_id": project},
        json={"username": username, "role": role},
    )
    assert response.status_code == 201, response.text
    url: str = response.json()["invite_url"]
    return "/" + url.split("://", 1)[1].split("/", 1)[1]


def _accept(app: FastAPI, path: str, password: str = "Client-Pass-12345!") -> int:
    with TestClient(app) as anon:
        form = anon.get(path)
        if form.status_code != 200:
            return int(form.status_code)
        return int(
            anon.post(
                path,
                data={
                    "csrf_token": extract_csrf(form.text),
                    "password": password,
                    "password_confirm": password,
                },
                follow_redirects=False,
            ).status_code
        )


def test_invitation_is_single_use_hashed_and_binds_client(
    app: FastAPI, client: TestClient, project: int, db_session: Session
) -> None:
    path = _invite(client, project, "eta-client")
    token = path.rsplit("/", 1)[1]
    stored = db_session.execute(select(UserInvitation)).scalar_one()
    assert token not in stored.token_hash  # only the hash is stored
    assert _accept(app, path) == 303
    assert _accept(app, path) == 404  # second use refused
    user = db_session.execute(select(User).where(User.username == "eta-client")).scalar_one()
    assert user.client_id is not None and not user.is_org_admin
    events = {e.event_type for e in db_session.execute(select(AuditLogEntry)).scalars()}
    assert {AuditEventType.INVITATION_CREATED, AuditEventType.INVITATION_ACCEPTED} <= events


def test_expired_and_revoked_invitations_fail(
    app: FastAPI, client: TestClient, project: int, db_session: Session
) -> None:
    expired = _invite(client, project, "late-user")
    invitation = db_session.execute(select(UserInvitation)).scalar_one()
    invitation.expires_at = utcnow() - timedelta(minutes=1)
    db_session.commit()
    assert _accept(app, expired) == 404

    revoked = _invite(client, project, "revoked-user")
    pending = client.get("/api/v1/invitations", params={"workspace_id": project}).json()
    assert [p["username"] for p in pending] == ["revoked-user"]
    assert (
        client.delete(
            f"/api/v1/invitations/{pending[0]['id']}", params={"workspace_id": project}
        ).status_code
        == 204
    )
    assert _accept(app, revoked) == 404
    assert _accept(app, "/invite/not-a-real-token") == 404


def test_weak_password_and_mismatch_are_rejected(
    app: FastAPI, client: TestClient, project: int
) -> None:
    path = _invite(client, project, "weak-user")
    with TestClient(app) as anon:
        form = anon.get(path)
        token = extract_csrf(form.text)
        mismatch = anon.post(
            path,
            data={"csrf_token": token, "password": "A-Good-Pass-123", "password_confirm": "x"},
        )
        assert mismatch.status_code == 422
        weak = anon.post(
            path, data={"csrf_token": token, "password": "short", "password_confirm": "short"}
        )
        assert weak.status_code == 422
    assert _accept(app, path) == 303  # still usable after failed attempts


def test_cannot_invite_existing_account_or_client_role_without_client(
    client: TestClient, admin_user: TestUser
) -> None:
    internal = client.post("/api/v1/workspaces", json={"name": "Internal"}).json()["id"]
    response = client.post(
        "/api/v1/invitations",
        params={"workspace_id": internal},
        json={"username": "someone", "role": "client"},
    )
    assert response.status_code == 422
    response = client.post(
        "/api/v1/invitations",
        params={"workspace_id": internal},
        json={"username": admin_user.username, "role": "analyst"},
    )
    assert response.status_code == 422


def test_invitation_tokens_are_redacted_from_access_logs() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/invite/SeCrEtToKeN123?x=1", "1.1", 200),
        None,
    )
    SecretPathRedactingFilter().filter(record)
    message = record.getMessage()
    assert "SeCrEtToKeN123" not in message
    assert "/invite/[redacted]" in message


# --- demo ----------------------------------------------------------------------


def test_demo_reset_refuses_a_real_database(db_session: Session, admin_user: TestUser) -> None:
    with pytest.raises(DemoResetRefusedError):
        reset_demo_data(db_session)
    db_session.rollback()
    assert db_session.execute(select(func.count()).select_from(User)).scalar_one() == 1


def test_demo_seed_and_reset_on_an_empty_database(app: FastAPI) -> None:
    session = app.state.sessionmaker()
    try:
        summary = reset_demo_data(session)
        session.commit()
        assert is_demo_database(session)
        assert DEMO_ADMIN in summary.users
        first_ids = set(session.execute(select(User.id)).scalars())
        reset_demo_data(session)  # second reset wipes and reseeds
        session.commit()
        assert session.execute(select(func.count()).select_from(User)).scalar_one() == len(
            first_ids
        )
    finally:
        session.close()
    with TestClient(app) as presenter:
        login = presenter.post(
            "/api/v1/auth/login", json={"username": DEMO_ADMIN, "password": demo_password()}
        )
        assert login.status_code == 200
        overview = presenter.get("/overview")
        assert "Kestrel" in overview.text and "Bellwether" in overview.text
    with TestClient(app) as kestrel:
        kestrel.post(
            "/api/v1/auth/login",
            json={"username": "kestrel-client", "password": demo_password()},
        )
        portal = kestrel.get("/portal", follow_redirects=True)
        assert "Kestrel" in portal.text
        assert "Bellwether" not in portal.text
        assert "same template engine" not in portal.text  # internal comment stays internal


def test_web_demo_reset_requires_demo_mode(app: FastAPI, client: TestClient) -> None:
    workspace = client.post("/api/v1/workspaces", json={"name": "Real work"}).json()["id"]
    page = client.get(f"/settings/members?workspace_id={workspace}")
    assert "Reset demo data" not in page.text
    response = client.post(
        f"/settings/demo-reset?workspace_id={workspace}",
        data={"csrf_token": extract_csrf(page.text)},
    )
    assert response.status_code == 404


def test_web_demo_reset_in_demo_mode_reseeds(initialized_settings: Settings) -> None:
    settings = initialized_settings.model_copy(update={"demo_mode": True})
    app = create_app(settings=settings)
    session = app.state.sessionmaker()
    reset_demo_data(session)
    session.commit()
    session.close()
    with TestClient(app) as presenter:
        presenter.post(
            "/api/v1/auth/login", json={"username": DEMO_ADMIN, "password": demo_password()}
        )
        workspace = presenter.get("/api/v1/workspaces").json()["items"][0]["id"]
        page = presenter.get(f"/settings/members?workspace_id={workspace}")
        assert "DEMO" in page.text and "Reset demo data" in page.text
        response = presenter.post(
            f"/settings/demo-reset?workspace_id={workspace}",
            data={"csrf_token": extract_csrf(page.text)},
            follow_redirects=False,
        )
        assert response.status_code == 303


# --- backup / restore ------------------------------------------------------------


def _cli(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "threnda.app.cli", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_cli_backup_and_restore_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "live.db"
    env = {
        **os.environ,
        "THRENDA_DATABASE_URL": f"sqlite:///{db_path}",
        "THRENDA_SECRET_KEY": "test-secret",
        "THRENDA_AI_PROVIDER": "disabled",
        "THRENDA_DEMO_MODE": "true",
    }
    migrate = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert migrate.returncode == 0, migrate.stderr
    seeded = _cli("demo", "reset", "--yes", env=env)
    assert seeded.returncode == 0, seeded.stderr

    backup = tmp_path / "backup.db"
    result = _cli("backup", str(backup), env=env)
    assert result.returncode == 0, result.stderr
    assert oct(backup.stat().st_mode & 0o777) == "0o600"
    assert _cli("backup", str(backup), env=env).returncode == 1  # never overwrites

    # Simulate data loss, then restore.
    db_path.write_bytes(b"corrupted")
    restored = _cli("restore", str(backup), "--yes", env=env)
    assert restored.returncode == 0, restored.stderr
    assert (tmp_path / "live.db.before-restore").read_bytes() == b"corrupted"

    settings = Settings(  # type: ignore[call-arg]
        database_url=f"sqlite:///{db_path}",
        secret_key="test-secret",  # noqa: S106
        ai_provider=AIProvider.DISABLED,
        _env_file=None,
    )
    with TestClient(create_app(settings=settings)) as check:
        login = check.post(
            "/api/v1/auth/login", json={"username": DEMO_ADMIN, "password": demo_password()}
        )
        assert login.status_code == 200
        projects = check.get("/api/v1/workspaces").json()["items"]
        assert len(projects) == 2
        # Attachments are inside the database, so they survive a restore.
        exposures = check.get(
            "/api/v1/exposures", params={"workspace_id": projects[0]["id"], "q": "TLS"}
        ).json()["items"]
        files = check.get(
            f"/api/v1/exposures/{exposures[0]['id']}/attachments",
            params={"workspace_id": projects[0]["id"]},
        ).json()
        assert [f["filename"] for f in files] == ["gateway-tls-config-diff.txt"]

    not_a_backup = tmp_path / "junk.db"
    not_a_backup.write_text("hello")
    assert _cli("restore", str(not_a_backup), "--yes", env=env).returncode == 1


def test_demo_cli_refuses_without_demo_mode(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "THRENDA_DATABASE_URL": f"sqlite:///{tmp_path / 'x.db'}",
        "THRENDA_DEMO_MODE": "false",
    }
    result = _cli("demo", "reset", "--yes", env=env)
    assert result.returncode == 1
    assert "THRENDA_DEMO_MODE" in result.stderr
