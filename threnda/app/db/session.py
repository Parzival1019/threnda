"""Engine and session construction.

Threnda does not keep a module-global engine. ``create_app()`` builds one
engine and one sessionmaker per application instance and stores them on
``app.state`` (see ``threnda.app.main``). This is what lets the test suite
run each test against its own isolated SQLite file without any monkeypatching
of global state.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from threnda.app.core.config import Settings


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Turn on SQLite foreign key enforcement for every new connection.

    SQLite parses and stores ``FOREIGN KEY`` clauses but does not enforce
    them unless ``PRAGMA foreign_keys=ON`` is issued per-connection. Without
    this, invalid foreign keys would silently succeed on SQLite while
    failing on PostgreSQL — exactly the kind of database-specific surprise
    the schema is meant to avoid.
    """
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_engine_from_settings(settings: Settings) -> Engine:
    """Create a SQLAlchemy engine for the given settings."""
    connect_args: dict[str, Any] = {}
    if settings.is_sqlite:
        connect_args["check_same_thread"] = False

    engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
    _enable_sqlite_foreign_keys(engine)
    return engine


def create_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to the given engine."""
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def iter_session(sessionmaker_: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Yield a request-scoped session, committing on success and rolling back on error.

    Used as the body of the FastAPI ``get_db`` dependency (see
    ``threnda.app.api.deps``); factored out separately so it can also be
    used outside of a request context (e.g. scripts, tests) if needed.
    """
    session = sessionmaker_()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
