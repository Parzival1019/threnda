"""Deterministic text similarity for Layer 2 (fuzzy) deduplication.

Uses the standard library's ``difflib`` rather than a fuzzy-matching
dependency: deterministic (same inputs always produce the same score, no
model weights to drift), and "keep dependencies reasonable" per
docs/community-v0.1-spec.md, "DEDUPLICATION".
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_PUNCTUATION_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Normalize a title for stable comparison: lowercase, drop punctuation,
    collapse whitespace. Two titles differing only in case, punctuation or
    spacing normalize to the same string.
    """
    lowered = title.strip().lower()
    without_punctuation = _PUNCTUATION_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", without_punctuation).strip()


def title_similarity(a: str, b: str) -> float:
    """Deterministic similarity ratio in ``[0.0, 1.0]`` between two titles."""
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()
