"""Webhook delivery: SSRF address checks, signatures, timeouts, redirects.

DNS resolution is monkeypatched (never a real network lookup — Community
must stay fully offline-capable in its test suite) so every address-range
case is deterministic. Actual HTTP delivery is tested against a real local
``http.server`` instance bound to 127.0.0.1 with an ephemeral port — this
exercises the genuine ``httpx`` request/response path (timeouts, redirect
handling, exact signed bytes) without any mocking of the HTTP layer itself.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from sqlalchemy.orm import Session

from threnda.app.core.config import Environment, Settings
from threnda.app.models.enums import WorkspaceRole
from threnda.app.models.user import User
from threnda.app.models.webhook_endpoint import WebhookEndpoint
from threnda.app.models.workspace import Workspace
from threnda.app.services.crypto import decrypt_secret
from threnda.app.services.memberships import add_member
from threnda.app.services.user_accounts import create_user
from threnda.app.services.webhooks import (
    InvalidWebhookUrlError,
    create_webhook,
    deliver_webhook,
    validate_webhook_url,
)


def _settings(
    *,
    env: Environment = Environment.DEVELOPMENT,
    webhook_allow_private_targets: bool = False,
    webhook_timeout_seconds: float = 5.0,
) -> Settings:
    # `_env_file=None`: hermetic against whatever real `.env` a developer
    # happens to have checked out (e.g. one left over from a manual Docker
    # Compose smoke test) — see the identical rationale on the `settings`
    # fixture in tests/conftest.py.
    return Settings(  # type: ignore[call-arg]
        secret_key="webhook-test-secret",  # noqa: S106
        env=env,
        webhook_allow_private_targets=webhook_allow_private_targets,
        webhook_timeout_seconds=webhook_timeout_seconds,
        _env_file=None,
    )


# ---------------------------------------------------------------------------
# URL / SSRF validation — DNS resolution is monkeypatched.
# ---------------------------------------------------------------------------


def _fake_getaddrinfo(addr_map: dict[str, list[str]]) -> Any:
    def _resolve(host: str, *_args: object, **_kwargs: object) -> list[tuple[Any, ...]]:
        if host not in addr_map:
            raise OSError(f"no fake address configured for {host!r}")
        return [(2, 1, 6, "", (ip, 0)) for ip in addr_map[host]]

    return _resolve


@pytest.mark.parametrize(
    "hostname,address",
    [
        ("internal.example.test", "10.0.0.5"),  # RFC1918 private
        ("loopback.example.test", "127.0.0.1"),  # loopback
        ("metadata.example.test", "169.254.169.254"),  # cloud metadata / link-local
        ("linklocal.example.test", "169.254.1.1"),  # link-local
        ("multicast.example.test", "224.0.0.1"),  # multicast
        ("reserved.example.test", "240.0.0.1"),  # reserved
        ("unspecified.example.test", "0.0.0.0"),  # unspecified  # noqa: S104
    ],
)
def test_disallowed_address_ranges_are_rejected(
    monkeypatch: pytest.MonkeyPatch, hostname: str, address: str
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({hostname: [address]}))
    with pytest.raises(InvalidWebhookUrlError):
        validate_webhook_url(f"https://{hostname}/hook", settings=_settings())


def test_public_looking_address_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_getaddrinfo({"public.example.test": ["8.8.8.8"]}),
    )
    validate_webhook_url("https://public.example.test/hook", settings=_settings())


def test_one_unsafe_address_among_several_is_enough_to_reject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostname resolving to multiple addresses, only one of them private,
    is still rejected — every resolved address must be safe."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_getaddrinfo({"mixed.example.test": ["203.0.113.10", "10.0.0.1"]}),
    )
    with pytest.raises(InvalidWebhookUrlError):
        validate_webhook_url("https://mixed.example.test/hook", settings=_settings())


def test_allow_private_targets_override_disables_the_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"localhost": ["127.0.0.1"]}))
    validate_webhook_url(
        "http://localhost:9999/hook", settings=_settings(webhook_allow_private_targets=True)
    )


def test_unresolvable_host_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({}))
    with pytest.raises(InvalidWebhookUrlError):
        validate_webhook_url("https://does-not-resolve.example.test/hook", settings=_settings())


def test_plain_http_is_rejected_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_getaddrinfo({"public.example.test": ["203.0.113.10"]}),
    )
    with pytest.raises(InvalidWebhookUrlError):
        validate_webhook_url(
            "http://public.example.test/hook", settings=_settings(env=Environment.PRODUCTION)
        )


def test_plain_http_is_allowed_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_getaddrinfo({"public.example.test": ["8.8.8.8"]}),
    )
    validate_webhook_url("http://public.example.test/hook", settings=_settings())


def test_credentials_embedded_in_url_are_rejected() -> None:
    with pytest.raises(InvalidWebhookUrlError):
        validate_webhook_url("https://user:pass@example.test/hook", settings=_settings())


def test_non_http_scheme_is_rejected() -> None:
    with pytest.raises(InvalidWebhookUrlError):
        validate_webhook_url("ftp://example.test/hook", settings=_settings())


def test_url_with_no_hostname_is_rejected() -> None:
    with pytest.raises(InvalidWebhookUrlError):
        validate_webhook_url("https:///hook", settings=_settings())


# ---------------------------------------------------------------------------
# Real delivery, against a local HTTP server.
# ---------------------------------------------------------------------------


class _RecordingHandler(BaseHTTPRequestHandler):
    received: list[dict[str, Any]] = []
    behavior = "ok"

    def do_POST(self) -> None:  # noqa: N802 - stdlib method name
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        _RecordingHandler.received.append({"headers": dict(self.headers), "body": body})
        if self.behavior == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/should-not-be-followed")
            self.end_headers()
        elif self.behavior == "slow":
            time.sleep(2)
            self.send_response(200)
            self.end_headers()
        elif self.behavior == "error":
            self.send_response(500)
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

    def log_message(self, *_args: object) -> None:  # silence stdlib access logging
        pass


@pytest.fixture
def local_server() -> Iterator[ThreadingHTTPServer]:
    _RecordingHandler.received = []
    _RecordingHandler.behavior = "ok"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _endpoint_for(
    db_session: Session, workspace: Workspace, actor: User, url: str, settings: Settings
) -> WebhookEndpoint:
    endpoint, _secret = create_webhook(
        db_session, workspace, url=url, description=None, settings=settings, actor=actor
    )
    endpoint.enabled = True
    db_session.flush()
    return endpoint


def _seed_workspace_and_admin(db_session: Session) -> tuple[Workspace, User]:
    workspace = Workspace(name="webhook-test-workspace")
    db_session.add(workspace)
    db_session.flush()
    user = create_user(db_session, "webhookadmin", "Webhook-Admin-Pass1!", actor=None)
    add_member(db_session, workspace, user, WorkspaceRole.ADMIN, actor=user)
    db_session.flush()
    return workspace, user


def test_delivery_signs_the_exact_request_body(
    db_session: Session, local_server: ThreadingHTTPServer
) -> None:
    workspace, user = _seed_workspace_and_admin(db_session)
    settings = _settings(webhook_allow_private_targets=True)
    url = f"http://127.0.0.1:{local_server.server_address[1]}/hook"
    endpoint = _endpoint_for(db_session, workspace, user, url, settings)

    result = deliver_webhook(db_session, endpoint, settings=settings, actor=user)

    assert result.ok is True
    assert result.status_code == 200
    assert len(_RecordingHandler.received) == 1
    received = _RecordingHandler.received[0]
    secret = decrypt_secret(settings.secret_key, endpoint.secret_ciphertext)
    expected_signature = (
        "sha256=" + hmac.new(secret.encode(), received["body"], hashlib.sha256).hexdigest()
    )
    assert received["headers"]["X-Threnda-Signature"] == expected_signature
    payload = json.loads(received["body"])
    assert payload["event"] == "test"
    assert payload["workspace_id"] == workspace.id
    assert payload["triggered_by"] == user.username


def test_delivery_records_a_failed_status_without_raising(
    db_session: Session, local_server: ThreadingHTTPServer
) -> None:
    _RecordingHandler.behavior = "error"
    workspace, user = _seed_workspace_and_admin(db_session)
    settings = _settings(webhook_allow_private_targets=True)
    url = f"http://127.0.0.1:{local_server.server_address[1]}/hook"
    endpoint = _endpoint_for(db_session, workspace, user, url, settings)

    result = deliver_webhook(db_session, endpoint, settings=settings, actor=user)

    assert result.ok is False
    assert result.status_code == 500
    assert endpoint.last_status_code == 500


def test_redirect_is_never_followed(db_session: Session, local_server: ThreadingHTTPServer) -> None:
    _RecordingHandler.behavior = "redirect"
    workspace, user = _seed_workspace_and_admin(db_session)
    settings = _settings(webhook_allow_private_targets=True)
    url = f"http://127.0.0.1:{local_server.server_address[1]}/hook"
    endpoint = _endpoint_for(db_session, workspace, user, url, settings)

    result = deliver_webhook(db_session, endpoint, settings=settings, actor=user)

    # A 302 is recorded as the delivery result itself — it is not treated
    # as success, and the (deliberately unreachable) redirect target is
    # never contacted.
    assert result.status_code == 302
    assert result.ok is False


def test_delivery_times_out(db_session: Session, local_server: ThreadingHTTPServer) -> None:
    _RecordingHandler.behavior = "slow"
    workspace, user = _seed_workspace_and_admin(db_session)
    settings = _settings(webhook_allow_private_targets=True, webhook_timeout_seconds=0.2)
    url = f"http://127.0.0.1:{local_server.server_address[1]}/hook"
    endpoint = _endpoint_for(db_session, workspace, user, url, settings)

    result = deliver_webhook(db_session, endpoint, settings=settings, actor=user)

    assert result.ok is False
    assert result.status_code is None
    assert result.error is not None and "timed out" in result.error.lower()


def test_delivery_is_re_validated_against_ssrf_immediately_before_sending(
    db_session: Session, local_server: ThreadingHTTPServer
) -> None:
    """Even an endpoint saved while pointed at an allowed target must be
    re-checked at delivery time (docs/security.md's "checked... again
    immediately before every delivery")."""
    workspace, user = _seed_workspace_and_admin(db_session)
    settings = _settings(webhook_allow_private_targets=True)
    url = f"http://127.0.0.1:{local_server.server_address[1]}/hook"
    endpoint = _endpoint_for(db_session, workspace, user, url, settings)

    stricter_settings = _settings(webhook_allow_private_targets=False)
    with pytest.raises(InvalidWebhookUrlError):
        deliver_webhook(db_session, endpoint, settings=stricter_settings, actor=user)


def test_webhook_secret_is_never_the_plaintext_in_storage(db_session: Session) -> None:
    workspace, user = _seed_workspace_and_admin(db_session)
    settings = _settings(webhook_allow_private_targets=True)
    endpoint, secret = create_webhook(
        db_session,
        workspace,
        url="http://127.0.0.1:9/hook",
        description=None,
        settings=settings,
        actor=user,
    )
    assert secret not in endpoint.secret_ciphertext
    assert decrypt_secret(settings.secret_key, endpoint.secret_ciphertext) == secret
