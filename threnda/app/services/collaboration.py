"""Comments, evidence attachments and client-side actions on a finding.

Visibility rule (docs/security.md, "Client users"): a ``client``-role
member only ever sees comments/attachments that are *not* internal, and
only the client-relevant subset of a finding's history. Every read path in
the portal and the API goes through the ``visible_*`` helpers here, so the
rule lives in one place. A client can never create internal content.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.attachment import Attachment
from threnda.app.models.enums import ExposureHistoryEventType, ExposureStatus, WorkspaceRole
from threnda.app.models.exposure import Exposure
from threnda.app.models.exposure_comment import ExposureComment
from threnda.app.models.exposure_history import ExposureHistory
from threnda.app.models.user import User
from threnda.app.services.exposures import apply_exposure_update

MAX_COMMENT_LENGTH = 10_000

#: Status changes a client-side user may make: "we're working on it" and
#: "we fixed it — please retest". Nothing else (no closing, no risk
#: acceptance, no reopening) — see docs/finding-lifecycle.md.
CLIENT_ALLOWED_STATUSES = frozenset({ExposureStatus.IN_PROGRESS, ExposureStatus.FIX_REPORTED})

#: History event types a client-side user sees. Excludes internal working
#: notes, correlation bookkeeping, the acknowledged flag, comment/attachment
#: bookkeeping (the visible comments/attachments are listed directly), and
#: every ``system`` (import-differential) event — see ``visible_history``.
CLIENT_VISIBLE_HISTORY = frozenset(
    {
        ExposureHistoryEventType.CREATED,
        ExposureHistoryEventType.STATUS_CHANGED,
        ExposureHistoryEventType.OWNER_CHANGED,
        ExposureHistoryEventType.REMEDIATION_DATE_CHANGED,
        ExposureHistoryEventType.REMEDIATION_CHANGED,
        ExposureHistoryEventType.DETAILS_CHANGED,
        ExposureHistoryEventType.FIX_REPORTED,
        ExposureHistoryEventType.RETEST_PASSED,
        ExposureHistoryEventType.RETEST_FAILED,
        ExposureHistoryEventType.REOPENED,
    }
)


class CollaborationError(ValueError):
    """A user-correctable problem with a comment, upload or client action."""


def is_client(role: WorkspaceRole) -> bool:
    return role == WorkspaceRole.CLIENT


# --- comments ---------------------------------------------------------------


def add_comment(
    db: Session,
    exposure: Exposure,
    *,
    body: str,
    internal: bool,
    actor: User,
    actor_role: WorkspaceRole,
) -> ExposureComment:
    text = (body or "").strip()
    if not text:
        raise CollaborationError("A comment cannot be empty.")
    if len(text) > MAX_COMMENT_LENGTH:
        raise CollaborationError(f"A comment can be at most {MAX_COMMENT_LENGTH} characters.")
    if is_client(actor_role):
        internal = False
    comment = ExposureComment(
        workspace_id=exposure.workspace_id,
        exposure_id=exposure.id,
        author_user_id=actor.id,
        author_username=actor.username,
        body=text,
        is_internal=internal,
    )
    db.add(comment)
    db.add(
        ExposureHistory(
            exposure=exposure,
            event_type=ExposureHistoryEventType.COMMENT_ADDED,
            new_value="internal comment" if internal else "comment",
            actor=actor.username,
        )
    )
    db.flush()
    return comment


def visible_comments(
    db: Session, exposure: Exposure, role: WorkspaceRole
) -> Sequence[ExposureComment]:
    stmt = select(ExposureComment).where(
        ExposureComment.workspace_id == exposure.workspace_id,
        ExposureComment.exposure_id == exposure.id,
    )
    if is_client(role):
        stmt = stmt.where(ExposureComment.is_internal.is_(False))
    return db.execute(stmt.order_by(ExposureComment.created_at, ExposureComment.id)).scalars().all()


def visible_history(exposure: Exposure, role: WorkspaceRole) -> list[ExposureHistory]:
    history = list(exposure.history)
    if not is_client(role):
        return history
    return [
        event
        for event in history
        if event.event_type in CLIENT_VISIBLE_HISTORY and event.actor != "system"
    ]


# --- client-side status actions --------------------------------------------


def client_set_status(
    db: Session,
    exposure: Exposure,
    *,
    status: ExposureStatus,
    note: str | None,
    actor: User,
) -> None:
    """A client marks a finding in progress or fixed (awaiting retest).

    The optional note becomes a normal (client-visible) comment, so the
    pentester sees *what* was fixed alongside the status change.
    """
    if status not in CLIENT_ALLOWED_STATUSES:
        raise CollaborationError(
            "Client users can only mark a finding as in progress or as fixed (ready for retest)."
        )
    if exposure.status == status:
        raise CollaborationError(f"This finding is already {status.value.replace('_', ' ')}.")
    apply_exposure_update(db, exposure, {"status": status}, note=None, actor=actor.username)
    if note and note.strip():
        add_comment(
            db, exposure, body=note, internal=False, actor=actor, actor_role=WorkspaceRole.CLIENT
        )


# --- attachments --------------------------------------------------------------


@dataclass(frozen=True)
class _AllowedType:
    content_type: str
    magic: tuple[bytes, ...] = ()
    text: bool = False


#: Allowlist by extension; the content must also match (magic bytes, or
#: valid UTF-8 text without NUL bytes). Anything a browser could execute or
#: render as active content (HTML, SVG, XML, JS) and every archive/binary
#: format is refused. See docs/attachments.md.
ALLOWED_ATTACHMENT_TYPES: dict[str, _AllowedType] = {
    ".png": _AllowedType("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ".jpg": _AllowedType("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": _AllowedType("image/jpeg", (b"\xff\xd8\xff",)),
    ".gif": _AllowedType("image/gif", (b"GIF87a", b"GIF89a")),
    ".pdf": _AllowedType("application/pdf", (b"%PDF-",)),
    ".txt": _AllowedType("text/plain", text=True),
    ".log": _AllowedType("text/plain", text=True),
    ".md": _AllowedType("text/plain", text=True),
    ".csv": _AllowedType("text/csv", text=True),
    ".json": _AllowedType("application/json", text=True),
}

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._ ()-]+")


def sanitize_attachment_filename(raw: str | None) -> str:
    name = raw or ""
    # Strip any client-side directory component, for both path styles.
    name = PureWindowsPath(PurePosixPath(name).name).name
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = _UNSAFE_FILENAME_CHARS.sub("_", name).strip(" .")
    if not name:
        name = "attachment"
    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 200 - len(ext) - 1] + dot + ext) if dot else name[:200]
    return name


def _extension(filename: str) -> str:
    _, dot, ext = filename.rpartition(".")
    return f".{ext.lower()}" if dot else ""


def validate_attachment(filename: str, raw: bytes, *, max_bytes: int) -> str:
    """Return the verified content type, or raise ``CollaborationError``."""
    if not raw:
        raise CollaborationError("The file is empty.")
    if len(raw) > max_bytes:
        raise CollaborationError(
            f"The file is too large ({len(raw):,} bytes); the limit is {max_bytes:,} bytes."
        )
    ext = _extension(filename)
    allowed = ALLOWED_ATTACHMENT_TYPES.get(ext)
    if allowed is None:
        accepted = ", ".join(sorted(ALLOWED_ATTACHMENT_TYPES))
        raise CollaborationError(
            f"File type {ext or '(none)'!r} is not allowed. Allowed: {accepted}."
        )
    if allowed.magic and not any(raw.startswith(m) for m in allowed.magic):
        raise CollaborationError(
            f"The file's content does not match its {ext} extension — upload the original file."
        )
    if allowed.text:
        if b"\x00" in raw:
            raise CollaborationError(f"A {ext} file must be plain text.")
        try:
            decoded = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CollaborationError(f"A {ext} file must be UTF-8 text.") from exc
        if ext == ".json":
            try:
                json.loads(decoded)
            except ValueError as exc:
                raise CollaborationError("The .json file is not valid JSON.") from exc
    return allowed.content_type


def add_attachment(
    db: Session,
    exposure: Exposure,
    *,
    filename: str | None,
    raw: bytes,
    internal: bool,
    actor: User,
    actor_role: WorkspaceRole,
    max_bytes: int,
) -> Attachment:
    clean_name = sanitize_attachment_filename(filename)
    content_type = validate_attachment(clean_name, raw, max_bytes=max_bytes)
    if is_client(actor_role):
        internal = False
    attachment = Attachment(
        workspace_id=exposure.workspace_id,
        exposure_id=exposure.id,
        filename=clean_name,
        content_type=content_type,
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        content=raw,
        is_internal=internal,
        uploaded_by_user_id=actor.id,
        uploaded_by_username=actor.username,
    )
    db.add(attachment)
    db.add(
        ExposureHistory(
            exposure=exposure,
            event_type=ExposureHistoryEventType.ATTACHMENT_ADDED,
            new_value=f"{'internal ' if internal else ''}attachment {clean_name}",
            actor=actor.username,
        )
    )
    db.flush()
    return attachment


def visible_attachments(
    db: Session, exposure: Exposure, role: WorkspaceRole
) -> Sequence[Attachment]:
    stmt = select(Attachment).where(
        Attachment.workspace_id == exposure.workspace_id,
        Attachment.exposure_id == exposure.id,
    )
    if is_client(role):
        stmt = stmt.where(Attachment.is_internal.is_(False))
    return db.execute(stmt.order_by(Attachment.created_at, Attachment.id)).scalars().all()


def get_visible_attachment(
    db: Session, workspace_id: int, attachment_id: int, role: WorkspaceRole
) -> Attachment | None:
    """One attachment, scoped to the caller's workspace *and* visibility.

    ``None`` both for "doesn't exist", "belongs to another workspace" and
    "internal, caller is a client" — the routes answer all three with the
    same 404 so none of them can be told apart.
    """
    attachment = db.get(Attachment, attachment_id)
    if attachment is None or attachment.workspace_id != workspace_id:
        return None
    if attachment.is_internal and is_client(role):
        return None
    return attachment
