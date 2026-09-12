"""ASGI-level request size enforcement.

Implemented as middleware wrapping ``receive()``, not as a dependency
checking the ``Content-Length`` header: a client can omit ``Content-Length``
entirely (chunked transfer encoding) or simply lie about it, and
Starlette's multipart parser will read and buffer the body before any
FastAPI dependency or route handler gets a chance to reject it. Wrapping
``receive()`` lets the request be aborted mid-stream, before an oversized
upload is ever fully read into memory — the limit is checked against bytes
actually received, never against a header.

The exception raised is ``starlette.exceptions.HTTPException`` specifically,
not a bespoke exception type. FastAPI's own request-body-reading code
(``fastapi/routing.py``, the code that calls ``await request.form()`` for a
file upload) wraps that call in a broad ``except Exception: raise
HTTPException(400, "There was an error parsing the body")`` — but it
special-cases ``except HTTPException: raise`` *before* that broad handler,
re-raising an ``HTTPException`` unchanged. A bespoke exception type would be
swallowed by the broad handler and surface as an unhelpful 400 instead of a
413; ``HTTPException`` is the one exception type guaranteed to pass through
untouched all the way to Starlette's built-in exception-handling middleware,
which turns it into the correct response.
"""

from __future__ import annotations

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class MaxBodySizeMiddleware:
    """Reject any HTTP request whose body exceeds ``max_bytes``, as it streams in."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        total = 0
        max_bytes = self.max_bytes

        async def limited_receive() -> Message:
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Request body too large (limit is {max_bytes} bytes).",
                    )
            return message

        await self.app(scope, limited_receive, send)
