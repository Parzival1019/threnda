"""Unit tests for the deterministic Layer 2 similarity helpers."""

from __future__ import annotations

from threnda.app.services.dedup.similarity import normalize_title, title_similarity
from threnda.app.services.ingestion.fingerprint import compute_fingerprint


def test_normalize_title_ignores_case_punctuation_and_whitespace() -> None:
    assert normalize_title("Outdated TLS Configuration!") == "outdated tls configuration"
    assert normalize_title("  Outdated   TLS-Configuration  ") == "outdated tls configuration"


def test_identical_titles_have_similarity_one() -> None:
    assert title_similarity("Outdated TLS configuration", "Outdated TLS configuration") == 1.0


def test_titles_differing_only_in_formatting_are_still_identical() -> None:
    assert title_similarity("Outdated TLS configuration", "outdated tls configuration!!!") == 1.0


def test_unrelated_titles_have_low_similarity() -> None:
    score = title_similarity(
        "Outdated TLS configuration on the web server",
        "SQL injection in the login form",
    )
    assert score < 0.5


def test_near_duplicate_titles_have_high_similarity() -> None:
    score = title_similarity(
        "Outdated TLS configuration on synthetic-web-01",
        "Outdated TLS configuration on synthetic-web-02",
    )
    assert score > 0.9


def test_similarity_is_symmetric() -> None:
    a, b = "Outdated TLS configuration", "Weak TLS configuration"
    assert title_similarity(a, b) == title_similarity(b, a)


def test_fingerprint_is_deterministic_for_identical_inputs() -> None:
    assert compute_fingerprint("CVE-2099-1", "host:a.example.test", "Title") == compute_fingerprint(
        "CVE-2099-1", "host:a.example.test", "Title"
    )


def test_fingerprint_is_stable_across_case_and_whitespace() -> None:
    assert compute_fingerprint(
        "cve-2099-1", " host:a.example.test ", "TITLE"
    ) == compute_fingerprint("CVE-2099-1", "host:a.example.test", "title")


def test_fingerprint_changes_when_an_identifying_part_changes() -> None:
    base = compute_fingerprint("CVE-2099-1", "host:a.example.test", "Title")
    different_cve = compute_fingerprint("CVE-2099-2", "host:a.example.test", "Title")
    different_asset = compute_fingerprint("CVE-2099-1", "host:b.example.test", "Title")
    assert base != different_cve
    assert base != different_asset
