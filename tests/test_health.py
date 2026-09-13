"""Health endpoint tests.

The endpoint is a readiness check, not a bare liveness probe — it must
distinguish an actually-migrated database from one that merely responds to
``SELECT 1``. ``test_health_reports_ok`` uses the real Alembic CLI (see
``migrated_client`` in conftest.py) precisely because the point being tested
is migration state, which the faster ``create_all``-based fixtures don't
produce (no ``alembic_version`` table at all).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from threnda.app.core.config import Settings
from threnda.app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_health_reports_ok_for_a_fully_migrated_database(migrated_client: TestClient) -> None:
    response = migrated_client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert "version" in body


def test_health_reports_uninitialized_for_a_database_with_no_migrations(
    client: TestClient,
) -> None:
    """The ``client`` fixture's database is schema-complete via ``create_all``
    but was never touched by Alembic — no ``alembic_version`` row exists.
    That must read as not-ready, not as "ok", even though a plain
    ``SELECT 1`` would succeed against it.
    """
    response = client.get("/api/v1/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "uninitialized"


def test_health_reports_outdated_for_a_database_behind_head(tmp_path: Path) -> None:
    db_path = tmp_path / "outdated.db"
    env = dict(os.environ)
    env["THRENDA_DATABASE_URL"] = f"sqlite:///{db_path}"
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", "1c704bf9919a"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    settings = Settings(  # type: ignore[call-arg]
        database_url=f"sqlite:///{db_path}",
        secret_key="test-secret",
        _env_file=None,  # noqa: S106
    )

    with TestClient(create_app(settings=settings)) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 503
    body = response.json()
    assert body["database"] == "outdated"


def test_health_response_never_leaks_raw_database_error_text(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    body_text = response.text.lower()
    assert "sqlite3" not in body_text
    assert "operationalerror" not in body_text
    assert "traceback" not in body_text


def test_openapi_schema_is_served(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert "/api/v1/health" in schema["paths"]
    assert "/api/v1/exposures" in schema["paths"]
