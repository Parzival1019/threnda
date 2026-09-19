"""Resolve web assets from source checkouts and frozen macOS app bundles."""

from __future__ import annotations

import os
from pathlib import Path


def web_resource_dir(name: str) -> Path:
    bundle_root = os.environ.get("RESOURCEPATH")
    if bundle_root:
        bundled = Path(bundle_root) / name
        if bundled.is_dir():
            return bundled
    return Path(__file__).parent / name
