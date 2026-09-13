"""``threnda`` command-line entry point.

Currently one subcommand: ``bootstrap-admin`` — see docs/deployment.md,
"Bootstrapping the first admin". This is deliberately the *only* way a
Threnda instance ever gets its first account: there is no self-registration
page anywhere in the web UI, and this command refuses to run a second time
once any account exists.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import threnda.app.models  # noqa: F401 - registers all mappers on Base
from threnda.app.core.config import get_settings
from threnda.app.core.startup_checks import (
    InsecureConfigurationError,
    validate_production_readiness,
)
from threnda.app.db.migration_status import get_database_status
from threnda.app.db.session import create_engine_from_settings, create_sessionmaker
from threnda.app.models.enums import WorkspaceRole
from threnda.app.models.user import User
from threnda.app.models.workspace import Workspace
from threnda.app.services.auth import InvalidUsernameError, WeakPasswordError
from threnda.app.services.memberships import add_member
from threnda.app.services.user_accounts import UsernameTakenError, create_user

DEFAULT_WORKSPACE_NAME = "Default"


def _read_credentials() -> tuple[str, str]:
    """Read the bootstrap username/password from env vars, or prompt interactively.

    Never invents a credential (CLAUDE.md): in a non-interactive context
    (no TTY, e.g. a container entrypoint) with the environment variables
    unset, this exits with an error rather than generating anything.
    """
    env_username = os.environ.get("THRENDA_BOOTSTRAP_ADMIN_USERNAME", "").strip()
    env_password = os.environ.get("THRENDA_BOOTSTRAP_ADMIN_PASSWORD", "")
    if env_username and env_password:
        return env_username, env_password

    if not sys.stdin.isatty():
        print(
            "THRENDA_BOOTSTRAP_ADMIN_USERNAME and THRENDA_BOOTSTRAP_ADMIN_PASSWORD must both be "
            "set to bootstrap non-interactively (e.g. in a container entrypoint).",
            file=sys.stderr,
        )
        raise SystemExit(1)

    username = input("Admin username: ").strip()
    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match.", file=sys.stderr)
        raise SystemExit(1)
    return username, password


def bootstrap_admin() -> int:
    settings = get_settings()
    try:
        validate_production_readiness(settings)
    except InsecureConfigurationError as exc:
        print(f"Refusing to bootstrap: {exc}", file=sys.stderr)
        return 1

    engine = create_engine_from_settings(settings)
    session: Session = create_sessionmaker(engine)()
    try:
        db_status = get_database_status(session)
        if db_status != "ok":
            print(
                f"Database is not ready (status={db_status!r}). Run "
                "`alembic upgrade head` before bootstrapping.",
                file=sys.stderr,
            )
            return 1

        existing_users = session.execute(select(func.count()).select_from(User)).scalar_one()
        if existing_users > 0:
            print(
                "An account already exists — bootstrap is one-time only. Use an existing "
                "admin's Settings > Members page to add more accounts.",
                file=sys.stderr,
            )
            # 2, not 1: this is the expected, benign outcome every time the
            # container entrypoint re-runs bootstrap on a restart — not a
            # real failure the way a rejected weak password is.
            return 2

        username, password = _read_credentials()

        try:
            user = create_user(session, username, password, actor=None)
        except (InvalidUsernameError, WeakPasswordError, UsernameTakenError) as exc:
            print(f"Could not create account: {exc}", file=sys.stderr)
            session.rollback()
            return 1

        workspace = session.execute(
            select(Workspace).order_by(Workspace.id).limit(1)
        ).scalar_one_or_none()
        if workspace is None:
            workspace = Workspace(name=DEFAULT_WORKSPACE_NAME)
            session.add(workspace)
            session.flush()

        add_member(session, workspace, user, WorkspaceRole.ADMIN, actor=user)
        session.commit()
    finally:
        session.close()
        engine.dispose()

    print(f"Created admin account {user.username!r} in workspace {workspace.name!r}.")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="threnda")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "bootstrap-admin",
        help="Create the first local admin account (one-time; refuses if any account exists).",
    )
    args = parser.parse_args(argv)

    if args.command == "bootstrap-admin":
        raise SystemExit(bootstrap_admin())


if __name__ == "__main__":
    main()
