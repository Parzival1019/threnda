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


def demo_reset(assume_yes: bool) -> int:
    """Create or reset the fictional demo dataset (docs/demo.md)."""
    from threnda.app.services.demo import DemoResetRefusedError, demo_password, reset_demo_data

    settings = get_settings()
    if not settings.demo_mode:
        print(
            "Refusing: THRENDA_DEMO_MODE is not true. Demo data is only ever written to a "
            "separate demo instance — see docs/demo.md.",
            file=sys.stderr,
        )
        return 1
    if not assume_yes:
        answer = input(
            f"This deletes ALL data in {settings.database_url!r} and loads the fictional demo "
            "dataset. Type 'reset demo' to continue: "
        )
        if answer.strip() != "reset demo":
            print("Cancelled.", file=sys.stderr)
            return 1
    engine = create_engine_from_settings(settings)
    session: Session = create_sessionmaker(engine)()
    try:
        if get_database_status(session) != "ok":
            print("Database is not migrated. Run `alembic upgrade head` first.", file=sys.stderr)
            return 1
        try:
            summary = reset_demo_data(session)
        except DemoResetRefusedError as exc:
            session.rollback()
            print(f"Refusing: {exc}", file=sys.stderr)
            return 2
        session.commit()
    finally:
        session.close()
        engine.dispose()
    custom = bool(os.environ.get("THRENDA_DEMO_PASSWORD"))
    print("Demo dataset loaded (fictional data only).")
    print("Accounts: " + ", ".join(summary.users))
    print(
        "Password: the value of THRENDA_DEMO_PASSWORD"
        if custom
        else f"Password (demo-only default): {demo_password()}"
    )
    return 0


def backup_database(output: str) -> int:
    """Consistent online backup of a SQLite database (docs/backup-restore.md).

    PostgreSQL deployments use ``pg_dump`` instead — see that document.
    """
    import sqlite3

    settings = get_settings()
    if not settings.is_sqlite:
        print(
            "This command backs up SQLite databases. For PostgreSQL use pg_dump — see "
            "docs/backup-restore.md.",
            file=sys.stderr,
        )
        return 1
    source_path = settings.database_url.split("sqlite:///", 1)[1]
    target = Path(output)
    if target.exists():
        print(f"Refusing to overwrite existing file {target}.", file=sys.stderr)
        return 1
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    os.chmod(target, 0o600)
    print(f"Backup written to {target} (includes attachments; treat it as sensitive).")
    return 0


def restore_database(backup: str, assume_yes: bool) -> int:
    """Replace the SQLite database with a backup. Stop Threnda first."""
    import shutil
    import sqlite3

    settings = get_settings()
    if not settings.is_sqlite:
        print("For PostgreSQL use pg_restore — see docs/backup-restore.md.", file=sys.stderr)
        return 1
    source = Path(backup)
    if not source.is_file():
        print(f"No such file: {source}", file=sys.stderr)
        return 1
    check = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        has_version = check.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'alembic_version'"
        ).fetchone()[0]
    except sqlite3.DatabaseError as exc:
        print(f"Not a usable SQLite backup: {exc}", file=sys.stderr)
        return 1
    finally:
        check.close()
    if integrity != "ok" or not has_version:
        print("Not a Threnda database backup (integrity check or schema missing).", file=sys.stderr)
        return 1
    target = Path(settings.database_url.split("sqlite:///", 1)[1])
    if not assume_yes:
        answer = input(f"Replace {target} with {source}? Stop Threnda first. Type 'restore': ")
        if answer.strip() != "restore":
            print("Cancelled.", file=sys.stderr)
            return 1
    if target.exists():
        previous = target.with_name(target.name + ".before-restore")
        shutil.copy2(target, previous)
        print(f"Previous database kept at {previous}.")
    shutil.copy2(source, target)
    os.chmod(target, 0o600)
    print(f"Restored {source} -> {target}. Run `alembic upgrade head`, then start Threnda.")
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
    demo_parser = subparsers.add_parser("demo", help="Manage the fictional demo dataset.")
    demo_subparsers = demo_parser.add_subparsers(dest="demo_command", required=True)
    demo_reset_parser = demo_subparsers.add_parser(
        "reset", help="Delete everything and load the fictional demo data (demo mode only)."
    )
    demo_reset_parser.add_argument("--yes", action="store_true", help="Skip the confirmation.")
    backup_parser = subparsers.add_parser("backup", help="Back up a SQLite database.")
    backup_parser.add_argument("output", help="Path of the backup file to create.")
    restore_parser = subparsers.add_parser("restore", help="Restore a SQLite backup.")
    restore_parser.add_argument("backup", help="Backup file created by `threnda backup`.")
    restore_parser.add_argument("--yes", action="store_true", help="Skip the confirmation.")
    args = parser.parse_args(argv)
    if args.command == "demo" and args.demo_command == "reset":
        raise SystemExit(demo_reset(args.yes))
    if args.command == "backup":
        raise SystemExit(backup_database(args.output))
    if args.command == "restore":
        raise SystemExit(restore_database(args.backup, args.yes))

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
