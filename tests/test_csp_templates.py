"""Regression checks for the web UI's strict Content Security Policy."""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parents[1] / "threnda" / "app" / "web" / "templates"
INLINE_EVENT_HANDLER = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)


def test_templates_do_not_use_inline_event_handlers() -> None:
    offenders = []
    for template in TEMPLATE_DIR.rglob("*.html"):
        if INLINE_EVENT_HANDLER.search(template.read_text(encoding="utf-8")):
            offenders.append(template.relative_to(TEMPLATE_DIR).as_posix())

    assert offenders == []
