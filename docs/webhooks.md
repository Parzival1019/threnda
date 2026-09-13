# Outbound webhooks

Community's webhook feature is deliberately minimal, per
`docs/community-v0.1-spec.md`, "GENERIC WEBHOOK": a generic, admin-configured
delivery mechanism, not a vendor-specific integration (no Jira/Slack/ServiceNow
connector — that is Enterprise territory, see CLAUDE.md). There is no
background worker or queue anywhere in Community, so a delivery is a single,
synchronous, signed HTTP POST made inside the request that triggers it.

## What actually happens

There is **no automatic delivery on any event** (a new finding, a status
change, an import completing). An admin:

1. Configures an endpoint (**Settings → Webhooks**, or
   `POST /api/v1/webhooks`) with a URL and optional description.
   Threnda generates a random signing secret, encrypts it at rest, and shows
   it exactly once — copy it into the receiving system now, since it cannot
   be shown again (see [security.md](security.md), "Secret storage").
2. Enables it (endpoints are created disabled).
3. Explicitly clicks "Send test delivery" (or calls
   `POST /api/v1/webhooks/{id}/trigger`) whenever they want to send one.

Each delivery POSTs this JSON body:

```json
{
  "event": "test",
  "workspace_id": 1,
  "workspace_name": "Default",
  "triggered_by": "admin",
  "triggered_at": "2026-01-01T12:00:00+00:00"
}
```

with headers:

```
Content-Type: application/json
X-Threnda-Event: test
X-Threnda-Signature: sha256=<hex hmac>
User-Agent: Threnda-Webhook/1.0
```

`X-Threnda-Signature` is `hmac_sha256(secret, raw_request_body).hexdigest()`,
prefixed with `sha256=`. Verify it by recomputing the same HMAC over the
exact bytes you received (before any JSON re-parsing/re-serialization, which
could change key order or whitespace) and comparing with a constant-time
comparison.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `THRENDA_WEBHOOK_TIMEOUT_SECONDS` | `5` | Hard timeout on the outbound request. |
| `THRENDA_WEBHOOK_MAX_RESPONSE_BYTES` | `65536` | The response body is streamed and discarded past this many bytes — Threnda never buffers an unbounded response into memory. |
| `THRENDA_WEBHOOK_ALLOW_PRIVATE_TARGETS` | `false` | **Local development/tests only.** Disables the SSRF address-range check entirely; never set in production. |

## SSRF defense

See [security.md](security.md), "Webhooks — SSRF defense" for the full
design and its documented residual limitation (a narrow DNS-rebinding race).
In short: HTTPS-only in production, private/loopback/link-local/reserved/
multicast targets blocked by default, redirects never followed.

## What this is not

- Not per-event delivery (finding created, status changed, import completed)
  — every delivery today is the one manually-triggered "test" event above.
  A richer event model is future work (see [roadmap.md](roadmap.md)) and
  would need a real delivery queue with retries, which is explicitly out of
  scope for Community's "no background worker" architecture.
- Not a retry/backoff mechanism — a failed delivery is recorded
  (`last_status_code`/`last_error` on the endpoint, plus an audit log entry)
  and nothing else happens automatically; trigger again manually.
- Not a vendor-specific integration of any kind.
