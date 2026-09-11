"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from threnda.app.db.session import iter_session


def get_db(request: Request) -> Generator[Session, None, None]:
    """Yield a database session bound to the current app's sessionmaker.

    The sessionmaker lives on ``app.state`` (set in ``create_app()``)
    rather than a module global, so each application instance — including
    one built per test with its own temporary database — gets its own
    sessions.
    """
    yield from iter_session(request.app.state.sessionmaker)
