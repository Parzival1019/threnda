"""Tests for local, deterministic PDF/text extraction.

No network, no OCR, no AI anywhere in this module — see
docs/pentest-and-ai.md.
"""

from __future__ import annotations

import pytest
from pdf_fixtures import build_blank_page_pdf, build_minimal_text_pdf, encrypt_pdf

from threnda.app.services.documents.extraction import (
    MAX_EXTRACTED_CHARACTERS,
    DocumentExtractionError,
    extract_text_from_pdf,
    extract_text_from_plain,
)


def test_pdf_extracts_text_per_page_in_order() -> None:
    raw = build_minimal_text_pdf([["First page line one", "Second line"], ["Second page"]])

    document = extract_text_from_pdf(raw)

    assert document.page_count == 2
    assert document.pages[0].number == 1
    assert document.pages[1].number == 2
    assert "First page line one" in document.pages[0].text
    assert "Second page" in document.pages[1].text
    assert document.truncated is False


def test_pdf_full_text_joins_pages() -> None:
    raw = build_minimal_text_pdf([["Alpha"], ["Beta"]])

    document = extract_text_from_pdf(raw)

    assert "Alpha" in document.full_text
    assert "Beta" in document.full_text


def test_malformed_pdf_raises_extraction_error() -> None:
    with pytest.raises(DocumentExtractionError):
        extract_text_from_pdf(b"%PDF-1.4\nthis is not a real pdf body: {}[]<<>>")


def test_truncated_pdf_bytes_raises_extraction_error() -> None:
    raw = build_minimal_text_pdf([["Some content here"]])
    with pytest.raises(DocumentExtractionError):
        extract_text_from_pdf(raw[: len(raw) // 2])


def test_pdf_with_empty_user_password_is_transparently_opened() -> None:
    """The common "restricted but not really protected" case: an empty user
    password, real owner password. Threnda must open these automatically."""
    raw = build_minimal_text_pdf([["Restricted but not secret"]])
    encrypted = encrypt_pdf(
        raw,
        user_password="",  # noqa: S106 - test fixture password, not a real secret
        owner_password="owner-secret",  # noqa: S106
    )

    document = extract_text_from_pdf(encrypted)

    assert "Restricted but not secret" in document.full_text


def test_pdf_with_real_user_password_is_rejected() -> None:
    """A genuine password-protected PDF must never be cracked or guessed —
    only reported as unreadable."""
    raw = build_minimal_text_pdf([["Actually secret content"]])
    encrypted = encrypt_pdf(
        raw,
        user_password="real-secret",  # noqa: S106 - test fixture password, not a real secret
        owner_password="owner-secret",  # noqa: S106
    )

    with pytest.raises(DocumentExtractionError, match="password-protected"):
        extract_text_from_pdf(encrypted)


def test_image_only_pdf_extracts_successfully_to_empty_pages() -> None:
    """A scanned/image-only PDF is never OCRed and never errors — it
    extracts honestly to empty text, exactly like the module docstring
    promises."""
    raw = build_blank_page_pdf(page_count=2)

    document = extract_text_from_pdf(raw)

    assert document.page_count == 2
    assert document.full_text.strip() == ""


def test_pdf_text_beyond_the_safety_cap_is_truncated() -> None:
    long_line = "A" * (MAX_EXTRACTED_CHARACTERS + 1000)
    raw = build_minimal_text_pdf([[long_line]])

    document = extract_text_from_pdf(raw)

    assert document.truncated is True
    assert len(document.full_text) <= MAX_EXTRACTED_CHARACTERS


def test_plain_text_extracts_as_a_single_page() -> None:
    document = extract_text_from_plain(b"Finding: Something\nSeverity: High\n")

    assert document.page_count == 1
    assert "Finding: Something" in document.full_text


def test_plain_text_handles_utf8_bom() -> None:
    document = extract_text_from_plain("Finding: Unicode café".encode("utf-8-sig"))

    assert "café" in document.full_text


def test_plain_text_non_utf8_bytes_raise_extraction_error() -> None:
    with pytest.raises(DocumentExtractionError):
        extract_text_from_plain(b"\xff\xfe\x00\x00not valid utf-8")


def test_plain_text_beyond_the_safety_cap_is_truncated() -> None:
    raw = ("line\n" * (MAX_EXTRACTED_CHARACTERS // 4)).encode()
    assert len(raw) > MAX_EXTRACTED_CHARACTERS

    document = extract_text_from_plain(raw)

    assert document.truncated is True
    assert len(document.full_text) == MAX_EXTRACTED_CHARACTERS


def test_empty_plain_text_extracts_to_one_empty_page() -> None:
    document = extract_text_from_plain(b"")

    assert document.page_count == 1
    assert document.full_text == ""
    assert document.truncated is False
