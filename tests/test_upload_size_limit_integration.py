"""Real multipart integration regression test for the upload size limit.

Reproduces the exact scenario an independent review found broken: with
``THRENDA_MAX_UPLOAD_BYTES`` set low, uploading the 1696-byte
``demo-data/json/synthetic-findings.json`` sample through the *real*
``POST /api/v1/imports`` route (real ``File``/``Form`` dependency
resolution, real multipart parsing) returned ``400``, not the documented
``413`` — because ``fastapi/routing.py`` wraps ``await request.form()`` in
a broad ``except Exception: raise HTTPException(400, ...)`` that swallowed
the bespoke exception the middleware used to raise. The generic ASGI-level
tests in ``tests/test_upload_middleware.py`` did not catch this: their
minimal endpoint takes a bare ``Request`` and calls ``request.body()``
itself, which never goes through that FastAPI body-reading wrapper at all.
Every test here goes through the real app, the real route, and (except one
control case) the real multipart encoder — the same code path a real
client hits.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.core.config import Settings
from threnda.app.db.base import Base
from threnda.app.db.session import create_engine_from_settings
from threnda.app.main import create_app
from threnda.app.models.enums import SourceType
from threnda.app.models.finding import Finding
from threnda.app.models.import_run import ImportRun
from threnda.app.models.source import Source
from threnda.app.models.workspace import Workspace

DEMO_JSON = Path(__file__).resolve().parents[1] / "demo-data" / "json" / "synthetic-findings.json"
LOW_LIMIT_BYTES = 1024


@pytest.fixture
def low_limit_app(tmp_path: Path) -> FastAPI:
    """A real app, identical to the ``app`` fixture, except with
    ``max_upload_bytes`` set low enough that the demo JSON fixture (1696
    bytes) exceeds it — reproducing the reported bug exactly.
    """
    db_path = tmp_path / "low_limit.db"
    settings = Settings(
        database_url=f"sqlite:///{db_path}",
        secret_key="test-secret",  # noqa: S106
        max_upload_bytes=LOW_LIMIT_BYTES,
    )
    engine = create_engine_from_settings(settings)
    Base.metadata.create_all(engine)
    engine.dispose()
    return create_app(settings=settings)


@pytest.fixture
def low_limit_client(low_limit_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(low_limit_app) as test_client:
        yield test_client


@pytest.fixture
def workspace_and_json_source(low_limit_app: FastAPI) -> tuple[int, int]:
    session: Session = low_limit_app.state.sessionmaker()
    workspace = Workspace(name="low-limit-workspace")
    session.add(workspace)
    session.flush()
    source = Source(workspace_id=workspace.id, name="low-limit-source", source_type=SourceType.JSON)
    session.add(source)
    session.commit()
    ids = (workspace.id, source.id)
    session.close()
    return ids


def _assert_nothing_was_written(low_limit_app: FastAPI) -> None:
    session: Session = low_limit_app.state.sessionmaker()
    try:
        assert session.scalars(select(ImportRun)).all() == []
        assert session.scalars(select(Finding)).all() == []
    finally:
        session.close()


def test_real_multipart_upload_over_limit_returns_413_not_400(
    low_limit_client: TestClient,
    low_limit_app: FastAPI,
    workspace_and_json_source: tuple[int, int],
) -> None:
    """The exact reported reproduction: a real File/Form multipart upload
    of a file larger than the limit, through the real route."""
    workspace_id, source_id = workspace_and_json_source
    content = DEMO_JSON.read_bytes()
    assert len(content) > LOW_LIMIT_BYTES, "fixture must actually exceed the test limit"

    response = low_limit_client.post(
        "/api/v1/imports",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("synthetic-findings.json", content, "application/json")},
    )

    assert response.status_code == 413, response.text
    assert "too large" in response.json()["detail"].lower()
    _assert_nothing_was_written(low_limit_app)


def test_real_multipart_upload_within_limit_still_succeeds(
    low_limit_client: TestClient, workspace_and_json_source: tuple[int, int]
) -> None:
    """Control case: confirm the limit doesn't reject legitimate small uploads."""
    workspace_id, source_id = workspace_and_json_source

    response = low_limit_client.post(
        "/api/v1/imports",
        params={"workspace_id": workspace_id},
        data={"source_id": str(source_id)},
        files={"file": ("f.json", b'{"findings": []}', "application/json")},
    )

    assert response.status_code == 201, response.text


def _build_multipart_body(*, source_id: int, filename: str, content: bytes) -> tuple[bytes, str]:
    """Hand-build a valid multipart/form-data body.

    Used only so the tests below can control the ``Content-Length`` header
    independently of the actual body — something httpx's ``files=``
    convenience wrapper always computes correctly for us, and so can't be
    used to simulate a client that omits or lies about it.
    """
    boundary = "ThrendaTestBoundary1234567890"
    parts = [
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="source_id"\r\n\r\n'
        f"{source_id}\r\n".encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\nContent-Type: application/json\r\n\r\n'.encode()
        + content
        + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def test_multipart_upload_with_lying_content_length_is_rejected(
    low_limit_client: TestClient,
    low_limit_app: FastAPI,
    workspace_and_json_source: tuple[int, int],
) -> None:
    """A client that understates Content-Length must still be rejected —
    the limit is enforced against bytes actually received, not the header."""
    workspace_id, source_id = workspace_and_json_source
    content = DEMO_JSON.read_bytes()
    body, content_type = _build_multipart_body(
        source_id=source_id, filename="synthetic-findings.json", content=content
    )
    assert len(body) > LOW_LIMIT_BYTES

    response = low_limit_client.post(
        f"/api/v1/imports?workspace_id={workspace_id}",
        content=body,
        headers={"content-type": content_type, "content-length": "5"},
    )

    assert response.status_code == 413, response.text
    _assert_nothing_was_written(low_limit_app)


def test_multipart_upload_without_content_length_header_is_rejected(
    low_limit_client: TestClient,
    low_limit_app: FastAPI,
    workspace_and_json_source: tuple[int, int],
) -> None:
    """A chunked-style upload (no Content-Length at all) must still be
    rejected, enforced purely from the bytes actually streamed in."""
    workspace_id, source_id = workspace_and_json_source
    content = DEMO_JSON.read_bytes()
    body, content_type = _build_multipart_body(
        source_id=source_id, filename="synthetic-findings.json", content=content
    )
    assert len(body) > LOW_LIMIT_BYTES

    def body_chunks() -> Iterator[bytes]:
        chunk_size = 128
        for offset in range(0, len(body), chunk_size):
            yield body[offset : offset + chunk_size]

    response = low_limit_client.post(
        f"/api/v1/imports?workspace_id={workspace_id}",
        content=body_chunks(),
        headers={"content-type": content_type},
    )

    assert response.status_code == 413, response.text
    _assert_nothing_was_written(low_limit_app)
