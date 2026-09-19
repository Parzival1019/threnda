"""Run Threnda Community as a self-contained macOS desktop application."""

from __future__ import annotations

import importlib
import os
import platform
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import uvicorn
from alembic import command
from alembic.config import Config

from threnda.app.core.config import Settings
from threnda.app.main import create_app

KEYRING_SERVICE = "com.threnda.community.desktop"
KEYRING_ACCOUNT = "instance-secret"
DEFAULT_DESKTOP_PORT = 8411


class DesktopUnavailableError(RuntimeError):
    """Raised when the native desktop runtime cannot start safely."""


class CredentialStore(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...


class DesktopServer(uvicorn.Server):
    """Uvicorn server owned by the GUI lifecycle, not process signals."""

    def install_signal_handlers(self) -> None:
        return


@dataclass(frozen=True)
class DesktopPaths:
    root: Path
    database: Path
    ai: Path
    logs: Path
    webview: Path


def desktop_paths(home: Path | None = None) -> DesktopPaths:
    override = os.environ.get("THRENDA_DESKTOP_DATA_DIR") if home is None else None
    root = (
        Path(override).expanduser()
        if override
        else (home or Path.home()) / "Library" / "Application Support" / "Threnda"
    )
    return DesktopPaths(
        root=root,
        database=root / "threnda.db",
        ai=root / "ai",
        logs=root / "logs",
        webview=root / "webview",
    )


def prepare_desktop_paths(paths: DesktopPaths) -> None:
    for directory in (paths.root, paths.ai, paths.logs, paths.webview):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)


def _system_keyring() -> CredentialStore:
    try:
        keyring = importlib.import_module("keyring")
    except ImportError as exc:  # pragma: no cover - only a broken packaged build
        raise DesktopUnavailableError("The macOS Keychain integration is unavailable.") from exc
    return cast(CredentialStore, keyring)


def get_or_create_instance_secret(store: CredentialStore | None = None) -> str:
    """Load the desktop instance secret from Keychain, creating it once if absent."""
    resolved_store = store or _system_keyring()
    try:
        existing = resolved_store.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        if existing and len(existing) >= 32:
            return existing
        generated = secrets.token_urlsafe(48)
        resolved_store.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, generated)
        return generated
    except Exception as exc:
        raise DesktopUnavailableError("Threnda could not access macOS Keychain safely.") from exc


def desktop_settings(paths: DesktopPaths, secret_key: str) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        secret_key=secret_key,
        database_url=f"sqlite:///{paths.database}",
        ai_data_dir=str(paths.ai),
    )


def _resource_root() -> Path:
    py2app_root = os.environ.get("RESOURCEPATH")
    if py2app_root:
        return Path(py2app_root)
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(str(frozen_root))
    return Path(__file__).resolve().parents[3]


def migrate_desktop_database(settings: Settings, resource_root: Path | None = None) -> None:
    root = resource_root or _resource_root()
    config_path = root / "alembic.ini"
    migrations = root / "migrations"
    if not config_path.is_file() or not migrations.is_dir():
        raise DesktopUnavailableError("The packaged database migrations are missing.")
    config = Config(str(config_path))
    config.set_main_option("script_location", str(migrations))
    config.attributes["database_url"] = settings.database_url
    command.upgrade(config, "head")


def _assert_port_available(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            candidate.bind(("127.0.0.1", port))
        except OSError as exc:
            raise DesktopUnavailableError(
                "Threnda is already running or its local port is unavailable."
            ) from exc


def run_desktop(*, port: int = DEFAULT_DESKTOP_PORT) -> None:
    """Start the private loopback server and display it in a native WebKit window."""
    if platform.system() != "Darwin":
        raise DesktopUnavailableError("Community Desktop currently supports macOS only.")

    paths = desktop_paths()
    prepare_desktop_paths(paths)
    settings = desktop_settings(paths, get_or_create_instance_secret())
    migrate_desktop_database(settings)
    _assert_port_available(port)

    setup_token = secrets.token_urlsafe(32)
    app = create_app(settings, desktop_setup_token=setup_token)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = DesktopServer(config)
    thread = threading.Thread(target=server.run, name="threnda-desktop-server", daemon=True)
    thread.start()

    deadline = time.monotonic() + 15
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        raise DesktopUnavailableError("Threnda's local server did not start.")

    try:
        webview = importlib.import_module("webview")
    except ImportError as exc:  # pragma: no cover - only a broken packaged build
        server.should_exit = True
        thread.join(timeout=5)
        raise DesktopUnavailableError(
            "The native macOS window integration is unavailable."
        ) from exc

    start_url = f"http://127.0.0.1:{port}/desktop/start?token={setup_token}"
    webview.create_window(
        "Threnda Community",
        start_url,
        width=1440,
        height=900,
        min_size=(1024, 700),
        background_color="#07111f",
        text_select=True,
    )
    try:
        webview.start(private_mode=False, storage_path=str(paths.webview))
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def main() -> None:
    try:
        run_desktop()
    except DesktopUnavailableError as exc:
        print(f"Threnda Desktop could not start: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
