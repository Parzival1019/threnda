"""``threnda`` command-line entry point.

Includes ``bootstrap-admin`` plus the managed local-AI lifecycle commands.
Bootstrap is deliberately the *only* way a
Threnda instance ever gets its first account: there is no self-registration
page anywhere in the web UI, and this command refuses to run a second time
once any account exists.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy.orm import Session

import threnda.app.models  # noqa: F401 - registers all mappers on Base
from threnda.app.core.config import get_settings
from threnda.app.core.startup_checks import (
    InsecureConfigurationError,
    validate_production_readiness,
)
from threnda.app.db.migration_status import get_database_status
from threnda.app.db.session import create_engine_from_settings, create_sessionmaker
from threnda.app.services.auth import InvalidUsernameError, WeakPasswordError
from threnda.app.services.bootstrap import (
    InstanceAlreadyInitializedError,
    create_first_admin,
    has_accounts,
)
from threnda.app.services.user_accounts import UsernameTakenError


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

        if has_accounts(session):
            print(
                "An account already exists — bootstrap is one-time only. Use an existing "
                "admin's Settings > Members page to add more accounts.",
                file=sys.stderr,
            )
            return 2

        username, password = _read_credentials()

        try:
            user, workspace = create_first_admin(session, username, password)
        except InstanceAlreadyInitializedError:
            print(
                "An account already exists — bootstrap is one-time only. Use an existing "
                "admin's Settings > Members page to add more accounts.",
                file=sys.stderr,
            )
            # 2, not 1: this is the expected, benign outcome every time the
            # container entrypoint re-runs bootstrap on a restart — not a
            # real failure the way a rejected weak password is.
            return 2
        except (InvalidUsernameError, WeakPasswordError, UsernameTakenError) as exc:
            print(f"Could not create account: {exc}", file=sys.stderr)
            session.rollback()
            return 1
        session.commit()
    finally:
        session.close()
        engine.dispose()

    print(f"Created admin account {user.username!r} in workspace {workspace.name!r}.")
    return 0


def ai_install(bundle: str | None = None) -> int:
    from threnda.app.services.ai.embedded import EmbeddedAIError, install_embedded_ai

    print("Installing Threnda Local AI (about 1.2 GB; this can take several minutes)…")
    try:
        status = install_embedded_ai(get_settings(), bundle_dir=Path(bundle) if bundle else None)
    except EmbeddedAIError as exc:
        print(f"Local AI installation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Local AI ready: {status.model} / llama.cpp {status.runtime_version}")
    return 0


def ai_status() -> int:
    from threnda.app.services.ai.embedded import status_dict

    status = status_dict(get_settings())
    for key in (
        "supported",
        "installed",
        "running",
        "installing",
        "stage",
        "model",
        "runtime_version",
        "error",
    ):
        print(f"{key}: {status[key]}")
    return 0


def ai_bundle(output: str, source: str | None = None) -> int:
    from threnda.app.services.ai.embedded import EmbeddedAIError, build_offline_bundle

    print("Building a verified offline Local AI bundle…")
    try:
        result = build_offline_bundle(
            Path(output), source_dir=Path(source).expanduser() if source else None
        )
    except EmbeddedAIError as exc:
        print(f"Local AI bundle failed: {exc}", file=sys.stderr)
        return 1
    print(f"Offline Local AI bundle ready: {result}")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="threnda")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "bootstrap-admin",
        help="Create the first local admin account (one-time; refuses if any account exists).",
    )
    ai_parser = subparsers.add_parser("ai", help="Manage Threnda's built-in local AI.")
    ai_subparsers = ai_parser.add_subparsers(dest="ai_command", required=True)
    install_parser = ai_subparsers.add_parser(
        "install", help="Install the pinned local runtime and model."
    )
    install_parser.add_argument(
        "--bundle",
        help="Read verified artifacts from an offline bundle directory instead of downloading.",
    )
    ai_subparsers.add_parser("status", help="Show local-AI installation and runtime status.")
    bundle_parser = ai_subparsers.add_parser(
        "bundle", help="Build a verified platform-specific offline AI bundle."
    )
    bundle_parser.add_argument("--output", required=True, help="Directory to create or update.")
    bundle_parser.add_argument(
        "--source", help="Optional directory containing already-downloaded verified artifacts."
    )
    args = parser.parse_args(argv)

    if args.command == "bootstrap-admin":
        raise SystemExit(bootstrap_admin())
    if args.command == "ai":
        if args.ai_command == "install":
            raise SystemExit(ai_install(args.bundle))
        if args.ai_command == "status":
            raise SystemExit(ai_status())
        if args.ai_command == "bundle":
            raise SystemExit(ai_bundle(args.output, args.source))


if __name__ == "__main__":
    main()
