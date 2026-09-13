"""Minimal, dependency-free PDF construction for tests and demo data.

Threnda has no runtime dependency capable of *writing* text-bearing PDFs
(``pypdf`` only reads/manipulates existing ones), and pulling one in just to
generate test fixtures would be a dependency added for tests, never used by
the application itself. This hand-builds the smallest valid PDF structure
(one indirect object per page/content-stream/font, a correct byte-offset
xref table, and a trailer) that is sufficient to exercise
``threnda.app.services.documents.extraction`` end to end against a real,
parseable PDF — not a mock of one.

Not itself a test module (no ``test_`` prefix) — pytest won't collect it.
"""

from __future__ import annotations

import io

from pypdf import PdfWriter

_HEADER = b"%PDF-1.4\n"


def _escape_pdf_string(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_minimal_text_pdf(pages: list[list[str]]) -> bytes:
    """Build a real, parseable multi-page PDF with one text line per row.

    ``pages`` is a list of pages, each a list of text lines drawn top to
    bottom with the standard (unembedded) Helvetica font — no font file, no
    external tool, just plain PDF syntax.
    """
    n_pages = len(pages)
    font_id = 3 + 2 * n_pages

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            f"<< /Type /Pages /Kids "
            f"[{' '.join(f'{3 + 2 * i} 0 R' for i in range(n_pages))}] "
            f"/Count {n_pages} >>"
        ).encode(),
    ]
    for index, lines in enumerate(pages):
        page_id = 3 + 2 * index
        content_id = page_id + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {content_id} 0 R >>"
            ).encode()
        )
        stream = "\n".join(
            f"BT /F1 12 Tf 72 {720 - 18 * line_index} Td ({_escape_pdf_string(line)}) Tj ET"
            for line_index, line in enumerate(lines)
        ).encode("latin-1", errors="replace")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    body = bytearray()
    offsets = [0]
    position = len(_HEADER)
    for object_number, obj in enumerate(objects, start=1):
        offsets.append(position)
        chunk = f"{object_number} 0 obj\n".encode() + obj + b"\nendobj\n"
        body += chunk
        position += len(chunk)

    xref_offset = position
    entry_count = len(objects) + 1
    xref = bytearray(f"xref\n0 {entry_count}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        xref += f"{offset:010d} 00000 n \n".encode()
    trailer = (
        f"trailer\n<< /Size {entry_count} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF"
    ).encode()

    return _HEADER + bytes(body) + bytes(xref) + trailer


def encrypt_pdf(raw: bytes, *, user_password: str, owner_password: str) -> bytes:
    """Re-encrypt an existing PDF's bytes with the given passwords."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password=user_password, owner_password=owner_password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def build_blank_page_pdf(page_count: int = 1) -> bytes:
    """A structurally valid PDF with no text content on any page —
    exactly what a scanned/image-only PDF's text layer looks like."""
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


MALFORMED_PDF_BYTES = b"%PDF-1.4\nthis is not a real pdf body: {}[]<<>>"
