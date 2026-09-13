"""Symmetric encryption for the one secret Threnda must store reversibly.

Everything else sensitive (passwords, session tokens) is stored as a
one-way hash — see ``threnda.app.services.auth``. A webhook signing secret
is different: Threnda must recover the plaintext to HMAC-sign each outgoing
delivery, so it cannot be hashed. It is encrypted at rest with Fernet
(AES-128-CBC + HMAC-SHA256, from the widely-used, actively maintained
``cryptography`` package) using a key derived from ``THRENDA_SECRET_KEY``.

Rotating ``THRENDA_SECRET_KEY`` makes every previously stored webhook
secret undecryptable — see docs/security.md, "Secret rotation". This is a
deliberate trade-off: introducing a second, independently-rotatable key
purely for this one field would be more moving parts than Community's
single generic webhook feature justifies.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

__all__ = ["InvalidToken", "decrypt_secret", "encrypt_secret"]


def _fernet_key(secret_key: str) -> bytes:
    digest = hashlib.sha256(f"threnda-webhook-secret:{secret_key}".encode()).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_secret(secret_key: str, plaintext: str) -> str:
    """Encrypt ``plaintext`` for storage, returning a text ciphertext."""
    return Fernet(_fernet_key(secret_key)).encrypt(plaintext.encode()).decode("ascii")


def decrypt_secret(secret_key: str, ciphertext: str) -> str:
    """Decrypt a value produced by :func:`encrypt_secret`.

    Raises ``cryptography.fernet.InvalidToken`` if ``secret_key`` does not
    match the key the value was encrypted with (e.g. after rotation).
    """
    return Fernet(_fernet_key(secret_key)).decrypt(ciphertext.encode()).decode()
