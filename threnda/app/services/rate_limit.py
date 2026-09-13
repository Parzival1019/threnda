"""A small in-process sliding-window rate limiter.

Community targets a single FastAPI process — the shipped
``docker-compose.yml`` runs one Uvicorn worker (see CLAUDE.md's 16 GB
Apple-Silicon resource target and "no Redis unless absolutely necessary").
A process-local counter is therefore sufficient as a coarse, IP-scoped
throttle on login attempts; it does not survive a process restart and does
not share state across multiple worker processes, which is a documented
limitation (docs/security.md, "Rate limiting") rather than an oversight.
The durable, restart-surviving control is the per-account lockout in
``threnda.app.services.auth`` (``User.failed_login_count`` /
``locked_until``); this limiter's job is only to also throttle attempts
against *unknown* usernames, which have no account row to lock.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """A fixed-size sliding-window counter, keyed by an arbitrary string."""

    def __init__(self, max_attempts: int, window_seconds: float) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _trim(self, hits: deque[float], now: float) -> None:
        while hits and now - hits[0] > self._window_seconds:
            hits.popleft()

    def hit(self, key: str) -> None:
        """Record one attempt for ``key``."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            self._trim(hits, now)
            hits.append(now)

    def is_limited(self, key: str) -> bool:
        """Whether ``key`` has hit the limit within the current window."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                return False
            self._trim(hits, now)
            return len(hits) >= self._max_attempts

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def clear(self) -> None:
        """Drop every tracked key. Used by tests to isolate the module-level
        ``login_ip_rate_limiter`` singleton between test cases — without
        this, one test that intentionally trips the limiter would leave it
        tripped for every later test sharing the same process (and, since
        ``TestClient`` requests all report the same source "IP", the same
        limiter key)."""
        with self._lock:
            self._hits.clear()


# 20 login attempts per 15 minutes per source IP, regardless of which
# username(s) were tried — see the module docstring for why this exists
# alongside the per-account lockout.
login_ip_rate_limiter = RateLimiter(max_attempts=20, window_seconds=900)
