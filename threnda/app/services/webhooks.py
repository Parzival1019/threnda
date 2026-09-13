"""Generic outbound webhook delivery.

Deliberately minimal, per docs/community-v0.1-spec.md — "GENERIC WEBHOOK":
one signed HTTP POST, delivered synchronously inside the request that
triggers it (Community has no background worker/queue), never fired
automatically on a finding/exposure event. An admin configures an
endpoint and a URL; an admin explicitly triggers a delivery. There is no
vendor-specific integration here.

SSRF defense (docs/security.md, "Webhooks — SSRF defense"):

- HTTPS is required in production (``Settings.is_production``); plain
  HTTP is allowed only in development, since a self-hosted test receiver
  during local development rarely has a certificate.
- The target hostname is resolved and every resulting address checked
  against private/loopback/link-local/reserved/multicast/unspecified
  ranges — covering the cloud metadata address (169.254.169.254 is
  link-local) without special-casing it — both when the endpoint is saved
  and again immediately before every delivery. ``THRENDA_WEBHOOK_ALLOW_
  PRIVATE_TARGETS`` disables this check entirely; it exists only for
  local development/tests pointing at a receiver on localhost and must
  never be set in production (``threnda.app.core.startup_checks`` does
  not special-case it, but docs/security.md tells operators not to).
- Redirects are never followed (``follow_redirects=False``): the classic
  SSRF-via-redirect bypass (point at an allowed public URL that 302s to
  an internal one) is fully closed, since Threnda simply records whatever
  3xx status the target returned as the delivery result.
- A resolve-then-connect DNS-rebinding race remains: re-checking
  immediately before each delivery narrows the window a target's DNS
  record could change between validation and connection, but does not
  eliminate it without a custom resolver pinning the connection to the
  exact address that was checked. Documented as a known limitation rather
  than silently assumed solved — see docs/security.md.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from threnda.app.core.config import Settings
from threnda.app.models.enums import AuditEventType
from threnda.app.models.user import User
from threnda.app.models.webhook_endpoint import WebhookEndpoint
from threnda.app.models.workspace import Workspace
from threnda.app.services.audit import record_audit_event
from threnda.app.services.crypto import decrypt_secret, encrypt_secret

USER_AGENT = "Threnda-Webhook/1.0"
SIGNATURE_HEADER = "X-Threnda-Signature"
EVENT_HEADER = "X-Threnda-Event"


class InvalidWebhookUrlError(ValueError):
    """Raised when a webhook URL fails validation or SSRF checks."""


def _unsafe_address(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _check_resolved_addresses(hostname: str, *, allow_private: bool) -> None:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise InvalidWebhookUrlError(f"Could not resolve host {hostname!r}.") from exc
    addresses = {str(info[4][0]) for info in infos}
    if not addresses:
        raise InvalidWebhookUrlError(f"Could not resolve host {hostname!r}.")
    if allow_private:
        return
    for addr in addresses:
        if _unsafe_address(addr):
            raise InvalidWebhookUrlError(
                f"Webhook target {hostname!r} resolves to a disallowed address ({addr})."
            )


def validate_webhook_url(url: str, *, settings: Settings) -> None:
    """Raise :class:`InvalidWebhookUrlError` unless ``url`` is safe to deliver to.

    Called both when an endpoint is saved and again immediately before
    every delivery — see the module docstring.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise InvalidWebhookUrlError("Webhook URL must be http or https.")
    if settings.is_production and parsed.scheme != "https":
        raise InvalidWebhookUrlError("Webhook URL must use HTTPS in production.")
    if not parsed.hostname:
        raise InvalidWebhookUrlError("Webhook URL must include a hostname.")
    if parsed.username or parsed.password:
        raise InvalidWebhookUrlError("Webhook URL must not embed credentials.")
    _check_resolved_addresses(parsed.hostname, allow_private=settings.webhook_allow_private_targets)


def create_webhook(
    db: Session,
    workspace: Workspace,
    *,
    url: str,
    description: str | None,
    settings: Settings,
    actor: User,
) -> tuple[WebhookEndpoint, str]:
    """Validate and persist a new endpoint. Returns the endpoint and its
    plaintext signing secret — the only time that plaintext value is ever
    available; store it in the receiving system now.
    """
    validate_webhook_url(url, settings=settings)
    secret = secrets.token_urlsafe(32)
    endpoint = WebhookEndpoint(
        workspace_id=workspace.id,
        url=url,
        description=description,
        secret_ciphertext=encrypt_secret(settings.secret_key, secret),
        enabled=False,
        created_by_user_id=actor.id,
    )
    db.add(endpoint)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.WEBHOOK_CREATED,
        actor=actor,
        workspace=workspace,
        target_type="webhook_endpoint",
        target_id=endpoint.id,
        detail={"url": url},
    )
    return endpoint, secret


def set_webhook_enabled(
    db: Session, endpoint: WebhookEndpoint, enabled: bool, *, actor: User
) -> None:
    endpoint.enabled = enabled
    record_audit_event(
        db,
        event_type=AuditEventType.WEBHOOK_UPDATED,
        actor=actor,
        workspace=endpoint.workspace,
        target_type="webhook_endpoint",
        target_id=endpoint.id,
        detail={"enabled": enabled},
    )


def delete_webhook(db: Session, endpoint: WebhookEndpoint, *, actor: User) -> None:
    workspace = endpoint.workspace
    endpoint_id = endpoint.id
    url = endpoint.url
    db.delete(endpoint)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.WEBHOOK_DELETED,
        actor=actor,
        workspace=workspace,
        target_type="webhook_endpoint",
        target_id=endpoint_id,
        detail={"url": url},
    )


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    status_code: int | None
    error: str | None


def deliver_webhook(
    db: Session,
    endpoint: WebhookEndpoint,
    *,
    settings: Settings,
    actor: User,
) -> DeliveryResult:
    """Send one signed test delivery, synchronously, and record the outcome.

    Never raises for a delivery failure (timeout, connection error,
    non-2xx) — that is a normal, expected result recorded on the endpoint
    and returned to the caller, not an application error. Raises
    :class:`InvalidWebhookUrlError` only if the URL itself fails
    validation (which the caller should treat as a configuration error).
    """
    validate_webhook_url(endpoint.url, settings=settings)

    secret = decrypt_secret(settings.secret_key, endpoint.secret_ciphertext)
    payload = {
        "event": "test",
        "workspace_id": endpoint.workspace_id,
        "workspace_name": endpoint.workspace.name,
        "triggered_by": actor.username,
        "triggered_at": datetime.now(UTC).isoformat(),
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "content-type": "application/json",
        SIGNATURE_HEADER: f"sha256={signature}",
        EVENT_HEADER: str(payload["event"]),
        "user-agent": USER_AGENT,
    }

    result: DeliveryResult
    try:
        with (
            httpx.Client(
                follow_redirects=False, timeout=settings.webhook_timeout_seconds
            ) as client,
            client.stream("POST", endpoint.url, content=body, headers=headers) as response,
        ):
            read = 0
            for chunk in response.iter_bytes():
                read += len(chunk)
                if read > settings.webhook_max_response_bytes:
                    break
            status_code = response.status_code
        result = DeliveryResult(ok=200 <= status_code < 300, status_code=status_code, error=None)
    except httpx.TimeoutException:
        result = DeliveryResult(ok=False, status_code=None, error="Delivery timed out.")
    except httpx.HTTPError as exc:
        result = DeliveryResult(ok=False, status_code=None, error=str(exc)[:500])

    endpoint.last_triggered_at = datetime.now(UTC)
    endpoint.last_status_code = result.status_code
    endpoint.last_error = result.error
    record_audit_event(
        db,
        event_type=AuditEventType.WEBHOOK_DELIVERY_TRIGGERED,
        actor=actor,
        workspace=endpoint.workspace,
        target_type="webhook_endpoint",
        target_id=endpoint.id,
        detail={"ok": result.ok, "status_code": result.status_code},
    )
    return result
