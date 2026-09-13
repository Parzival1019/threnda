"""Deterministic, fully local document text extraction.

No OCR, no AI, no network calls — see docs/pentest-and-ai.md, "PDF/text
extraction". ``pypdf`` reads only a PDF's existing text layer; a
scanned/image-only PDF with no embedded text simply extracts to empty
pages, reported honestly (empty excerpts, no candidates manufactured from
nothing), never silently "fixed" with OCR.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PdfReadError

# A courtesy cap distinct from the ASGI-level MaxBodySizeMiddleware (which
# already caps the *upload* itself, threnda.app.core.middleware): protects
# against a small file that decompresses/extracts to a pathological
# amount of text, independent of upload size.
MAX_EXTRACTED_CHARACTERS = 2_000_000


class DocumentExtractionError(Exception):
    """The document could not be read at all — malformed, truncated, or
    password-protected. Never raised for "no text found"; an empty or
    image-only document extracts successfully to empty pages instead."""


@dataclass(frozen=True)
class ExtractedPage:
    number: int  # 1-based
    text: str


@dataclass(frozen=True)
class ExtractedDocument:
    pages: tuple[ExtractedPage, ...]
    truncated: bool = False

    @property
    def full_text(self) -> str:
        return "\n\n".join(page.text for page in self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)


def _capped_pages(raw_pages: list[str]) -> tuple[tuple[ExtractedPage, ...], bool]:
    pages: list[ExtractedPage] = []
    total_chars = 0
    truncated = False
    for index, text in enumerate(raw_pages, start=1):
        if truncated:
            break
        remaining = MAX_EXTRACTED_CHARACTERS - total_chars
        if len(text) > remaining:
            text = text[:remaining]
            truncated = True
        total_chars += len(text)
        pages.append(ExtractedPage(number=index, text=text))
    return tuple(pages), truncated


def extract_text_from_pdf(raw: bytes) -> ExtractedDocument:
    """Extract per-page text from a PDF's existing text layer.

    Raises ``DocumentExtractionError`` for a malformed/truncated file or a
    PDF this process cannot decrypt (a real password was set — Threnda
    never attempts to guess or crack one; only an empty-user-password
    "restricted but not really protected" PDF is handled automatically).
    """
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            # The common case of a PDF "encrypted" only to restrict
            # editing, not to hide content, uses an empty user password.
            # Any other password is left alone — accessing pages below
            # will raise if this didn't actually unlock it.
            reader.decrypt("")
        raw_pages = [page.extract_text() or "" for page in reader.pages]
    except FileNotDecryptedError as exc:
        raise DocumentExtractionError(
            "This PDF is password-protected; Threnda cannot open encrypted PDFs."
        ) from exc
    except PdfReadError as exc:
        raise DocumentExtractionError(f"This file is not a valid PDF: {exc}") from exc

    pages, truncated = _capped_pages(raw_pages)
    return ExtractedDocument(pages=pages, truncated=truncated)


def extract_text_from_plain(raw: bytes) -> ExtractedDocument:
    """Decode a plain-text file as one "page" for a uniform shape.

    Line-level provenance (for candidate excerpts) comes from splitting
    this single page's text on ``\\n`` — see
    ``threnda.app.services.documents.section_detection``.
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentExtractionError(f"Not valid UTF-8 text: {exc}") from exc

    pages, truncated = _capped_pages([text])
    return ExtractedDocument(pages=pages, truncated=truncated)
