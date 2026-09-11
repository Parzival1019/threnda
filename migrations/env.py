"""Alembic environment.

The database URL is resolved from ``THRENDA_DATABASE_URL`` if set, falling
back to the application's normal ``Settings``. This lets ``alembic upgrade
head`` target a temporary database (as the test suite does) purely via an
environment variable, with no changes to ``alembic.ini``.
"""

from __future__ import annotations

import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import engine_from_config, pool

import threnda.app.models  # noqa: F401 - registers all mappers on Base
from threnda.app.core.config import get_settings
from threnda.app.db.base import Base
from threnda.app.models.types import UTCDateTime

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return os.environ.get("THRENDA_DATABASE_URL") or get_settings().database_url


def _render_item(type_: str, obj: Any, autogen_context: AutogenContext) -> str | bool:
    """Teach autogenerate how to import our custom ``UTCDateTime`` type.

    Without this, a generated migration references
    ``threnda.app.models.types.UTCDateTime(...)`` without importing
    ``threnda.app.models.types``, which is a ``NameError`` at migration run
    time. Returning ``False`` falls back to Alembic's default rendering for
    everything else.
    """
    if type_ == "type" and isinstance(obj, UTCDateTime):
        autogen_context.imports.add("from threnda.app.models.types import UTCDateTime")
        return "UTCDateTime()"
    return False


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.engine.url.get_backend_name() == "sqlite",
            render_item=_render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
