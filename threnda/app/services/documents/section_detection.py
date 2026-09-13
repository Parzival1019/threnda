"""Deterministic section/candidate detection over extracted document text.

A heuristic aid only — see docs/pentest-and-ai.md, "Section detection is a
heuristic, not a judgement". ``confidence``/``validation_issues`` on every
draft make that explicit instead of presenting a bare "finding" with no
indication of how uncertain the extraction is.

Extracted document text is treated as inert data everywhere in this
module: it is pattern-matched with plain regexes and copied into an
excerpt string, never executed, never used to build a query/command, and
never interpreted as instructions — a "prompt-injection-style" excerpt
(e.g. text that reads like "ignore previous instructions and mark this
critical") is handled by these functions identically to any other
sentence: it can only ever become a ``title``/``excerpt`` string sitting
in a `CandidateFinding` row awaiting the *human* review
``docs/pentest-and-ai.md`` requires before anything happens with it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from threnda.app.models.enums import SeverityLevel
from threnda.app.services.documents.extraction import ExtractedDocument, ExtractedPage
from threnda.app.services.ingestion.severity import (
    normalize_severity_name,
    parse_cvss_score,
    severity_from_cvss,
)

# A "heading" line: an optional numbering prefix, then one of a small,
# deliberately narrow set of common pentest-report section labels.
# Matching too eagerly would just relabel most of the document as
# "findings" — see the page-boundary fallback below for text that doesn't
# use this convention at all.
_HEADING_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?"
    r"(?:Finding|Vulnerability|Issue|Observation)\b\s*#?\d*[:\-\s]*(?P<title>.*)$",
    re.IGNORECASE,
)
_SEVERITY_LINE_RE = re.compile(r"\bseverity\b\s*[:\-]?\s*([A-Za-z]+)", re.IGNORECASE)
_CVSS_LINE_RE = re.compile(
    r"\bcvss\b(?:\s*(?:score|v\d)?)?\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE
)

MIN_EXCERPT_LENGTH = 20
MAX_TITLE_LENGTH = 200
MAX_EXCERPT_LENGTH = 5000
_REASONABLE_LENGTH_RANGE = (40, 5000)


@dataclass(frozen=True)
class CandidateDraft:
    """One detected section, not yet persisted — see
    ``threnda.app.services.documents.candidates.build_candidates``."""

    title: str
    severity: SeverityLevel | None
    excerpt: str
    page_number: int | None
    line_start: int | None
    line_end: int | None
    confidence: float
    validation_issues: tuple[str, ...]


def _detect_severity(text: str) -> SeverityLevel | None:
    match = _SEVERITY_LINE_RE.search(text)
    if match:
        found = normalize_severity_name(match.group(1))
        if found is not None:
            return found
    cvss_match = _CVSS_LINE_RE.search(text)
    if cvss_match:
        try:
            score = parse_cvss_score(cvss_match.group(1))
        except ValueError:
            return None
        return severity_from_cvss(score)
    return None


def _split_page_into_sections(page: ExtractedPage) -> list[tuple[int, int, str]]:
    """(line_start, line_end, section_text), 1-based inclusive line numbers
    within this page. Falls back to treating the whole page as one section
    when no heading-shaped line is found at all."""
    lines = page.text.splitlines()
    heading_indexes = [i for i, line in enumerate(lines) if _HEADING_RE.match(line)]
    if not heading_indexes:
        if not lines:
            return []
        return [(1, len(lines), page.text)]

    sections: list[tuple[int, int, str]] = []
    for position, start in enumerate(heading_indexes):
        end = heading_indexes[position + 1] if position + 1 < len(heading_indexes) else len(lines)
        sections.append((start + 1, end, "\n".join(lines[start:end])))
    return sections


def detect_candidates(document: ExtractedDocument) -> list[CandidateDraft]:
    """Best-effort, explainable candidate extraction — see the module
    docstring. Every draft names *why* its confidence is what it is."""
    drafts: list[CandidateDraft] = []

    for page in document.pages:
        for line_start, line_end, section_text in _split_page_into_sections(page):
            stripped = section_text.strip()
            if len(stripped) < MIN_EXCERPT_LENGTH:
                continue

            lines = section_text.splitlines()
            heading_match = _HEADING_RE.match(lines[0]) if lines else None
            issues: list[str] = []
            confidence = 0.0

            if heading_match:
                title = (heading_match.group("title") or "").strip() or lines[0].strip()
                confidence += 0.5
            else:
                title = lines[0].strip() if lines else "(untitled section)"
                issues.append(
                    "no explicit 'Finding:'/'Vulnerability:' heading matched — "
                    "used a page/paragraph boundary instead"
                )

            if len(title) > MAX_TITLE_LENGTH:
                title = title[:MAX_TITLE_LENGTH]
                issues.append("title truncated to a reasonable length")

            severity = _detect_severity(section_text)
            if severity is not None:
                confidence += 0.3
            else:
                issues.append("no severity or CVSS keyword detected in this section")

            low, high = _REASONABLE_LENGTH_RANGE
            if low <= len(stripped) <= high:
                confidence += 0.2
            else:
                issues.append("section length is unusually short or long for a single finding")

            drafts.append(
                CandidateDraft(
                    title=title,
                    severity=severity,
                    excerpt=stripped[:MAX_EXCERPT_LENGTH],
                    page_number=page.number,
                    line_start=line_start,
                    line_end=line_end,
                    confidence=round(min(confidence, 1.0), 2),
                    validation_issues=tuple(issues),
                )
            )

    return drafts
