"""Local authentication: passwords, sessions, and login orchestration.

Password hashing uses Argon2id via ``argon2-cffi`` — a vetted, actively
maintained implementation of the algorithm the OWASP Password Storage
Cheat Sheet recommends first, with that library's own default cost
parameters, rather than a hand-tuned or custom scheme. See
docs/security.md, "Passwords".

Session identifiers are opaque, high-entropy random tokens
(``secrets.token_urlsafe``); only their SHA-256 hash is ever persisted —
see ``threnda.app.models.user_session`` for why a fast hash is the right
choice for this value specifically, unlike a password.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from threnda.app.models.user import User
from threnda.app.models.user_session import UserSession

USERNAME_PATTERN = re.compile(r"^[a-z0-9._-]{3,64}$")
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256

# A small, well-known blocklist of extremely common passwords (all
# long-published in public breach-derived top-password lists — nothing
# proprietary or specific to any organization). This is a baseline check,
# not a substitute for length/complexity requirements.
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "123456789012",
        "1234567890123",
        "qwertyuiop123",
        "letmein123456",
        "admin12345678",
        "welcome123456",
        "changeme123456",
        "iloveyou123456",
        "trustno1234567",
        "monkey12345678",
        "dragon12345678",
    }
)

SESSION_IDLE_TIMEOUT = timedelta(hours=2)
SESSION_ABSOLUTE_TIMEOUT = timedelta(hours=12)
LOGIN_LOCKOUT_THRESHOLD = 5
LOGIN_LOCKOUT_DURATION = timedelta(minutes=15)

_hasher = PasswordHasher()
# Computed once per process against a fixed, non-secret string purely so
# authenticate() has something to hash-verify against on the "unknown
# username" path — see its docstring. It is not a real credential.
_DUMMY_HASH = _hasher.hash("threnda-constant-time-placeholder")


class InvalidUsernameError(ValueError):
    """Raised when a username fails the format policy."""


class WeakPasswordError(ValueError):
    """Raised when a password fails the strength policy."""


class InvalidCredentialsError(Exception):
    """Raised for any login failure that must look identical to the client.

    Deliberately used for both "no such user" and "wrong password" (and
    inactive accounts) — the HTTP/UI layer must render exactly the same
    generic message for all three, or the login form becomes a username
    oracle. Distinguish the real cause only in the audit log, server-side.
    """


class AccountLockedError(Exception):
    """Raised when an account is temporarily locked after repeated failures.

    Kept distinct from :class:`InvalidCredentialsError` for audit logging
    only — the HTTP/UI layer still renders the same generic message either
    way, so a client cannot use the response to tell "wrong password" apart
    from "locked out," which would otherwise reveal that the username exists.
    """


def normalize_username(raw: str) -> str:
    """Normalize a username for storage/lookup: trimmed, lowercased."""
    return raw.strip().lower()


def validate_username(raw: str) -> str:
    """Normalize and validate a username, raising ``InvalidUsernameError``.

    3-64 characters, lowercase letters/digits/``.``/``_``/``-`` only —
    deliberately restrictive so a username is always safe to use verbatim
    in logs, audit entries, and filenames without further escaping.
    """
    normalized = normalize_username(raw)
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise InvalidUsernameError(
            "Username must be 3-64 characters: lowercase letters, digits, '.', '_', or '-'."
        )
    return normalized


def validate_password_strength(password: str, *, username: str) -> None:
    """Raise ``WeakPasswordError`` unless ``password`` meets the baseline policy.

    Policy: 12-256 characters, at least two of
    {lowercase, uppercase, digit, symbol}, not equal to the username
    (case-insensitive), and not in a small common-password blocklist. This
    is a baseline, not a strength meter — Community deliberately does not
    pull in a heavier dependency (e.g. zxcvbn) for this.
    """
    if len(password) < PASSWORD_MIN_LENGTH:
        raise WeakPasswordError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters.")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise WeakPasswordError(f"Password must be at most {PASSWORD_MAX_LENGTH} characters.")
    if password.lower() == username.lower():
        raise WeakPasswordError("Password must not be the same as the username.")
    if password.lower() in _COMMON_PASSWORDS:
        raise WeakPasswordError("Password is too common. Choose a less predictable password.")
    classes = sum(
        bool(re.search(pattern, password))
        for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^a-zA-Z0-9]")
    )
    if classes < 2:
        raise WeakPasswordError(
            "Password must contain at least two of: lowercase, uppercase, digit, symbol."
        )


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _verify(hash_: str, password: str) -> bool:
    try:
        _hasher.verify(hash_, password)
    except VerifyMismatchError:
        return False
    return True


def authenticate(
    db: Session,
    username: str,
    password: str,
) -> User:
    """Verify credentials and return the matching, active ``User``.

    Always does one Argon2 verify — against the real user's hash if the
    username exists, against a fixed dummy hash otherwise — so a timing
    side channel cannot distinguish "unknown username" from "known
    username, wrong password". Raises :class:`InvalidCredentialsError` for
    every failure case except an active lockout, which raises
    :class:`AccountLockedError`; callers must render both identically to
    the end user (see those classes' docstrings) and rely on the audit log
    for the real reason.

    On success, resets the account's failure counter/lockout and
    transparently rehashes the password if Argon2's parameters have
    changed since it was last hashed. Does not commit — the caller's
    session lifecycle (the request-scoped ``get_db`` dependency) does.
    """
    normalized = normalize_username(username)
    user = db.execute(select(User).where(User.username == normalized)).scalar_one_or_none()

    if user is None:
        _verify(_DUMMY_HASH, password)
        raise InvalidCredentialsError("Invalid username or password.")

    now = datetime.now(UTC)
    if user.locked_until is not None and user.locked_until > now:
        _verify(_DUMMY_HASH, password)
        raise AccountLockedError("Account temporarily locked due to repeated failed logins.")

    password_ok = _verify(user.password_hash, password)

    if not password_ok or not user.is_active:
        if password_ok and not user.is_active:
            # A correct password for a deactivated account must still look
            # like a generic failure to the client.
            raise InvalidCredentialsError("Invalid username or password.")
        user.failed_login_count += 1
        if user.failed_login_count >= LOGIN_LOCKOUT_THRESHOLD:
            user.locked_until = now + LOGIN_LOCKOUT_DURATION
        raise InvalidCredentialsError("Invalid username or password.")

    user.failed_login_count = 0
    user.locked_until = None
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


@dataclass(frozen=True)
class IssuedSession:
    token: str
    session: UserSession


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(
    db: Session,
    user: User,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedSession:
    """Issue a brand-new session for ``user``.

    Always creates a new row with a freshly generated token — callers
    (the login route) never reuse or extend an existing session cookie,
    which is what makes a login a real *rotation*: any session id an
    attacker fixed in the browser before authentication becomes worthless.
    """
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    session = UserSession(
        user_id=user.id,
        token_hash=_hash_token(token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + SESSION_ABSOLUTE_TIMEOUT,
        user_agent=(user_agent or None) and user_agent[:300],
        ip_address=ip_address,
    )
    db.add(session)
    db.flush()
    return IssuedSession(token=token, session=session)


def validate_session(db: Session, token: str | None) -> UserSession | None:
    """Return the live ``UserSession`` for ``token``, or ``None``.

    Checks revocation, absolute expiry, idle expiry, and that the owning
    user is still active — any failure returns ``None`` rather than
    raising, since an expired/invalid session cookie is an ordinary,
    expected condition (the client is simply logged out), not an error.
    Touches ``last_seen_at`` on success (sliding idle window); does not
    commit.
    """
    if not token:
        return None
    session = db.execute(
        select(UserSession).where(UserSession.token_hash == _hash_token(token))
    ).scalar_one_or_none()
    if session is None or session.revoked_at is not None:
        return None
    now = datetime.now(UTC)
    if session.expires_at <= now:
        return None
    if now - session.last_seen_at > SESSION_IDLE_TIMEOUT:
        return None
    if not session.user.is_active:
        return None
    session.last_seen_at = now
    return session


def revoke_session(db: Session, session: UserSession) -> None:
    session.revoked_at = datetime.now(UTC)


def revoke_all_sessions_for_user(db: Session, user: User) -> None:
    now = datetime.now(UTC)
    for session in db.execute(
        select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
    ).scalars():
        session.revoked_at = now
