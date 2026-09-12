"""ASGI-level request size enforcement.

Deliberately implemented as middleware wrapping ``receive()``, not as a
dependency checking the ``Content-Length`` header: a client can omit
``Content-Length`` entirely (chunked transfer encoding) or simply lie about
it, and Starlette's multipart parser will read and buffer the body before
any FastAPI dependency or route handler gets a chance to reject it. Wrapping
``receive()`` lets the request be aborted mid-stream, before an oversized
upload is ever fully read into memory.
"""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class MaxBodySizeExceeded(Exception):
    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"Request body exceeded {max_bytes} bytes")
        self.max_bytes = max_bytes


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
                    raise MaxBodySizeExceeded(max_bytes)
            return message

        try:
            await self.app(scope, limited_receive, send)
        except MaxBodySizeExceeded:
            response = JSONResponse(
                {"detail": f"Request body too large (limit is {max_bytes} bytes)."},
                status_code=413,
            )
            await response(scope, receive, send)
