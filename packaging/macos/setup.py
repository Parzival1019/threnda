"""Build a self-contained Threnda Community macOS application."""

from __future__ import annotations

import sys
from pathlib import Path

from setuptools import setup

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

APP = [str(ROOT / "packaging" / "macos" / "Threnda.py")]
RESOURCES = [
    str(ROOT / "alembic.ini"),
    str(ROOT / "migrations"),
    str(ROOT / "threnda" / "app" / "web" / "static"),
    str(ROOT / "threnda" / "app" / "web" / "templates"),
]

OPTIONS = {
    "argv_emulation": False,
    "arch": "arm64",
    "packages": ["anyio", "threnda", "keyring", "uvicorn", "webview"],
    "includes": ["_cffi_backend"],
    # The desktop build is deliberately SQLite-only. Excluding the optional
    # PostgreSQL driver also keeps its Kerberos/OpenSSL client libraries out
    # of the app bundle.
    "excludes": [
        "mypy",
        "pip",
        "psycopg",
        "psycopg_binary",
        "pytest",
        "ruff",
        "setuptools",
    ],
    "resources": RESOURCES,
    "plist": {
        "CFBundleDisplayName": "Threnda",
        "CFBundleIdentifier": "com.threnda.community",
        "CFBundleName": "Threnda",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
    },
}

setup(
    name="Threnda",
    version="0.1.0",
    description="Private security finding management. Every finding. One queue.",
    app=APP,
    options={"py2app": OPTIONS},
)
