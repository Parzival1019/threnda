"""Tests for the ASGI request-size limit, independent of any specific route.

Verifies the limit is enforced by counting streamed bytes, not by trusting
``Content-Length`` — the middleware must reject an oversized body even when
the header is absent (as with chunked transfer encoding).
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from threnda.app.core.middleware import MaxBodySizeMiddleware


def _make_app(max_bytes: int) -> FastAPI:
    app = FastAPI()
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=max_bytes)

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        body = await request.body()
        return {"received": len(body)}

    return app


def test_body_within_limit_is_accepted() -> None:
    client = TestClient(_make_app(max_bytes=1024))

    response = client.post("/echo", content=b"x" * 100)

    assert response.status_code == 200
    assert response.json() == {"received": 100}


def test_body_over_limit_is_rejected_with_413() -> None:
    client = TestClient(_make_app(max_bytes=100))

    response = client.post("/echo", content=b"x" * 1000)

    assert response.status_code == 413


def test_body_over_limit_is_rejected_without_content_length_header() -> None:
    """A streaming/chunked body has no Content-Length at all — the limit
    must still be enforced from the actual bytes received."""

    def body_stream() -> Iterator[bytes]:
        for _ in range(20):
            yield b"x" * 100  # 2000 bytes total, over the 100-byte limit

    client = TestClient(_make_app(max_bytes=100))

    response = client.post("/echo", content=body_stream())

    assert response.status_code == 413
