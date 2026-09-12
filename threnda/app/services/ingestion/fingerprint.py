"""Provisional finding fingerprints.

`Finding.fingerprint` is `NOT NULL`, so every importer needs to produce
*some* stable value now, in Milestone 2 — but the layered deduplication
strategy that fingerprint is meant to feed (deterministic match, then fuzzy,
then optional semantic — see docs/community-v0.1-spec.md, "DEDUPLICATION")
is Milestone 3's job. This module is deliberately simple: a stable hash of
whichever identifying fields an importer actually has. Milestone 3 may
replace or extend this without changing the column itself.
"""

from __future__ import annotations

import hashlib


def compute_fingerprint(*parts: str | None) -> str:
    """Hash the given parts into a stable, opaque fingerprint.

    Parts are lowercased and stripped before hashing so that whitespace or
    casing differences between otherwise-identical re-imports don't change
    the fingerprint. Callers choose which parts to pass — e.g. CVE + asset
    key when a CVE is known, title + asset key otherwise — so the exact
    stability contract is per-importer, documented in docs/importers.md.
    """
    normalized = "\x1f".join((part or "").strip().lower() for part in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
