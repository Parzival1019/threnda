"""Keep one-time invitation tokens out of access logs.

An invitation link carries its secret in the URL path (``/invite/<token>``),
and uvicorn's access log records every request path. The token is
single-use and short-lived, but a log line must still never contain a
usable credential (docs/security.md, "Secrets and logging"). This filter
rewrites the path in uvicorn access-log records before they are emitted.
"""

from __future__ import annotations

import logging
import re

_INVITE_PATH = re.compile(r"(/invite/)[^/?#\s\"]+")
_REDACTED = r"\1[redacted]"


def redact_secrets_in_path(text: str) -> str:
    return _INVITE_PATH.sub(_REDACTED, text)


class SecretPathRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_secrets_in_path(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        if isinstance(record.msg, str):
            record.msg = redact_secrets_in_path(record.msg)
        return True


def install_access_log_redaction() -> None:
    """Idempotently attach the filter to uvicorn's access logger."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, SecretPathRedactingFilter) for f in logger.filters):
        logger.addFilter(SecretPathRedactingFilter())
