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

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)

_SECURITY_HEADERS: dict[bytes, bytes] = {
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
    b"referrer-policy": b"no-referrer",
    b"content-security-policy": _CSP.encode(),
    b"permissions-policy": b"geolocation=(), microphone=(), camera=()",
    b"cross-origin-opener-policy": b"same-origin",
}


class SecurityHeadersMiddleware:
    """Attach a baseline set of defensive response headers to every response.

    ``script-src 'self'`` with no ``unsafe-inline``/``unsafe-eval`` is safe
    here because the UI has no inline ``<script>`` tags or inline event
    handler attributes — htmx and the small vanilla-JS enhancement file are
    both loaded from ``/static`` (see ``threnda/app/web/static/js/app.js``).
    ``style-src`` allows ``unsafe-inline`` because templates use inline
    ``style="..."`` attributes in a few places; that is a far narrower
    attack surface than inline script and not worth a template-wide
    refactor for this milestone.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(_SECURITY_HEADERS.items())
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


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
