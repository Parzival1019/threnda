# Security model

This document describes Threnda Community's actual, implemented security
controls as of Milestone 8 / v0.1.0. It is not a claim of independent security audit,
and it does not describe planned Enterprise capabilities (SSO/SAML/OIDC/SCIM,
advanced RBAC, enterprise auditing) — see [roadmap.md](roadmap.md) and
CLAUDE.md for that boundary.

## Accounts and roles

Threnda Community has local username/password accounts only. There is no
self-registration page anywhere: the very first account is created once by
`threnda bootstrap-admin` (see [deployment.md](deployment.md)); every account
after that is created by an existing workspace admin on the
**Settings → Members** page (or `POST /api/v1/members`), which creates the
account and grants it a role in one step.

A user is a member of zero or more workspaces, with exactly one role per
workspace (`threnda.app.models.workspace_membership.WorkspaceMembership`):

| Role | Can |
| --- | --- |
| `viewer` | Read everything in the workspace: findings queue, exposure detail, imports, sources, documents, review queue, dashboard, audit log is admin-only (see below). |
| `analyst` | Everything `viewer` can, plus: acknowledge/assign/set target dates/change status/add notes on exposures, upload imports and documents, accept/reject candidate findings, merge/keep-separate possible duplicates, download CSV/JSON exports. |
| `admin` | Everything `analyst` can, plus: manage workspace members and their roles, configure and trigger webhooks, read the audit log. |

Roles are hierarchical (`threnda.app.services.memberships.role_at_least`):
admin implies analyst implies viewer. A workspace can never be left with zero
admins — demoting or removing the last admin is rejected
(`LastAdminError`). There is no superuser/global-admin concept: an admin's
authority is scoped to the workspaces they are a member of, and nobody can
manage a workspace they don't belong to, including the instance operator (the
operator's remedy for a locked-out workspace is direct database access, the
same as any other self-hosted single-tenant tool).

### Passwords

Hashed with **Argon2id** via the actively maintained `argon2-cffi` library,
using that library's own default cost parameters (`threnda.app.services.auth`)
— not a hand-tuned or custom scheme. A password's hash is transparently
upgraded on the next successful login if Argon2's parameters have changed
since it was last hashed (`check_needs_rehash`).

Baseline password policy (`validate_password_strength`): 12–256 characters,
at least two of {lowercase, uppercase, digit, symbol}, not equal to the
username, and not in a small blocklist of extremely common passwords. This is
a floor, not a strength meter — Community does not pull in a heavier
dependency (e.g. zxcvbn) for this.

Usernames are normalized (trimmed, lowercased) and restricted to
`[a-z0-9._-]{3,64}` so a username is always safe to use verbatim in logs,
audit entries, and filenames.

### Login: timing, lockout, and rate limiting

`threnda.app.services.auth.authenticate` always performs exactly one Argon2
verify per attempt — against the real user's hash if the username exists,
against a fixed dummy hash otherwise — so a timing side channel cannot tell
"unknown username" apart from "known username, wrong password". Every failure
case (unknown user, wrong password, locked account, deactivated account)
raises the same `InvalidCredentialsError`/`AccountLockedError` pair, and both
the JSON API and the web login form render the identical generic message
("Invalid username or password.") for all of them — the real reason is only
ever visible in the audit log.

Two independent, complementary controls:

- **Per-account lockout** (durable, survives a restart): after 5 consecutive
  failed logins, an account is locked for 15 minutes
  (`LOGIN_LOCKOUT_THRESHOLD`/`LOGIN_LOCKOUT_DURATION`), tracked on
  `User.failed_login_count`/`locked_until`. Resets on the next successful
  login.
- **Per-source-IP rate limit** (process-local, in-memory): 20 login attempts
  per 15 minutes per source IP, regardless of which username(s) were tried —
  `threnda.app.services.rate_limit.login_ip_rate_limiter`. This is what
  throttles credential spraying against *unknown* usernames, which have no
  account row to lock. It is intentionally a simple in-process sliding
  window: Community targets one Uvicorn worker (see CLAUDE.md's resource
  target and the shipped `docker-compose.yml`), so a process-local counter is
  sufficient without pulling in Redis. **Limitation:** it does not survive a
  process restart and is not shared across multiple worker processes — if
  you run Threnda with more than one worker, this control's effectiveness is
  reduced proportionally; the per-account lockout above is unaffected.

### Sessions

A session is an opaque, high-entropy random token (`secrets.token_urlsafe`,
256 bits), delivered as the `threnda_session` cookie. Only its SHA-256 hash
is ever persisted (`threnda.app.models.user_session.UserSession.token_hash`)
— the plaintext token is never written to the database, so reading a backup
or a database dump cannot be used to impersonate a session, the same
guarantee a password hash gives for the password itself. Hashing with a
fast, unsalted digest (rather than Argon2, as for passwords) is deliberate:
the token already has no guessable structure for a slow KDF to defend
against; the only question is whether reading this table lets you compute
what a client would send, and SHA-256 preimage resistance already answers
no.

- **Expiration**: an idle timeout (2 hours of inactivity) and a hard absolute
  cap (12 hours) — either one ends the session, whichever comes first
  (`SESSION_IDLE_TIMEOUT`/`SESSION_ABSOLUTE_TIMEOUT`).
- **Rotation on login**: every successful login issues a brand-new session
  row and token — an existing cookie (including one an attacker fixed in the
  victim's browser before authentication, i.e. session fixation) is never
  reused or extended.
- **Revocation**: logout marks the session `revoked_at` immediately
  (`threnda.app.services.auth.revoke_session`); a revoked or expired session
  fails validation the same way a garbage token would — no distinguishing
  error, no special-casing at the call site.
- **Cookie flags**: `HttpOnly` always; `SameSite=Strict` always;
  `Secure` whenever the deployment is production or the request itself is
  HTTPS (`threnda.app.web.cookies.cookie_secure`).
  `THRENDA_ALLOW_INSECURE_COOKIES=true` forces `Secure` off — a local
  development/testing override only; `threnda.app.core.startup_checks`
  refuses to start with it set in production.

### CSRF

Every state-changing web form requires a CSRF token bound to the
authenticated session (not a bare double-submit cookie): the token is
`HMAC-SHA256(THRENDA_SECRET_KEY, "csrf:" + session_token)`, recomputed on
every request from the session cookie the browser already sends and the
server's secret (`threnda.app.web.csrf`). It needs no storage or cookie of
its own, and it rotates automatically whenever the session does (e.g. on
login). Before a session exists (the login page itself), a plain anonymous
double-submit cookie is used instead, which still defeats a cross-site
login-CSRF attempt since a foreign origin cannot read the cookie to copy it.

The JSON API's own login/logout endpoints (`/api/v1/auth/*`) do not require a
CSRF token in addition to the session cookie: `SameSite=Strict` on its own
already prevents the cookie from being attached to a cross-site request at
all, and there is no browser-form submission vector to defend against for a
pure JSON endpoint the way there is for an HTML `<form>`.

### Response security headers

`threnda.app.core.middleware.SecurityHeadersMiddleware` attaches, on every
response: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`, a
restrictive `Permissions-Policy`, and a `Content-Security-Policy` with
`script-src 'self'` (no `unsafe-inline`/`unsafe-eval` — the UI has no inline
`<script>` tags or inline event-handler attributes; htmx and the one small
vanilla-JS file are both served from `/static`) and `style-src 'self'
'unsafe-inline'` (templates use inline `style="..."` attributes in a few
places — a narrower risk than inline script, not worth a template-wide
refactor for this milestone).

## Authorization

Every workspace-scoped API and web route depends on
`require_viewer`/`require_analyst`/`require_admin`
(`threnda.app.api.deps`/`threnda.app.web.deps`), never on a caller-supplied
`workspace_id` alone. `workspace_id` still appears as a request parameter — a
user can belong to more than one workspace — but it is not itself the
authority: access requires a matching `WorkspaceMembership` row for the
authenticated session's user. A `workspace_id` that doesn't exist and one
that exists but the caller isn't a member of are answered identically
(`404`), so neither response lets a caller enumerate other workspaces'
existence; an authenticated member with an insufficient role gets `403`
instead, which is safe to distinguish since reaching that check already
proves membership.

This is enforced identically for the JSON API and the server-rendered web
UI — the web layer's `get_current_membership` and the API's
`get_workspace_membership` are separate dependencies (a web-layer failure
redirects to `/login`; an API failure returns `401`/`404` JSON) but resolve
membership through the exact same query and the exact same
`role_at_least` hierarchy.

## Auditing

`threnda.app.services.audit.record_audit_event` is the single write path for
the administrative audit log (`AuditLogEntry`) — every call site goes through
it rather than constructing rows directly, so the "never a secret in
`detail`" rule (enforced defensively: any `detail` key containing
`password`/`secret`/`token`/`session`/`authorization`/`cookie`/`hash` is
rejected outright) has exactly one place to be tested. Audited events:
account creation, login success/failure, logout, membership add/role
change/remove, webhook create/update/delete/delivery, and export downloads.
Routine finding/exposure edits are **not** duplicated here — they already
have their own audit trail, `ExposureHistory`, unchanged since Milestone 1.
Raw finding content, passwords, session tokens, and webhook secrets are never
logged anywhere, audit log included.

The audit log is workspace-scoped and admin-only to read
(`GET /api/v1/audit-log`, **Settings → Audit log**). A few events (an
unknown-username login attempt) have no workspace and are recorded with
`workspace_id=NULL`; they are not currently surfaced in the per-workspace UI
(a documented limitation, not a data-loss — the row exists in the table).

## Uploads

Unchanged from Milestone 2/6, still in effect: a hard request-size cap
enforced at the ASGI layer before any multipart body is parsed
(`MaxBodySizeMiddleware`, `THRENDA_MAX_UPLOAD_BYTES`), filenames never used
for filesystem access, content validated against the source's declared
format, no shell execution anywhere, and no unsafe deserialization (JSON via
`json.loads`, PDF text extraction via `pypdf` with no macro/JS execution —
see [pentest-and-ai.md](pentest-and-ai.md)).

## Webhooks — SSRF defense

See [webhooks.md](webhooks.md) for the full delivery design. Summary of the
SSRF-relevant controls in `threnda.app.services.webhooks`:

- HTTPS required in production; plain HTTP allowed only in development.
- The target hostname is resolved and every resulting address checked
  against private/loopback/link-local/reserved/multicast/unspecified ranges
  (this covers the cloud metadata address `169.254.169.254` as an ordinary
  link-local address, with no special-casing needed) — both when the
  endpoint is saved and again immediately before every delivery.
  `THRENDA_WEBHOOK_ALLOW_PRIVATE_TARGETS=true` disables this check entirely;
  it exists only for local development/tests pointing at a receiver on
  localhost and **must never be set in production**.
- Redirects are never followed — the classic SSRF-via-redirect bypass (an
  allowed public URL that 302s to an internal one) is fully closed.
- **Known limitation**: a DNS-rebinding race remains between the
  pre-delivery validation and the actual connection a moment later.
  Re-checking immediately before each delivery narrows this window but does
  not eliminate it without a custom resolver pinning the connection to the
  exact address that was checked — genuinely solving that would need direct
  control over the TLS/TCP connection layer that this milestone's scope
  (a rarely-used, admin-only, manually-triggered generic webhook) does not
  justify. If this matters for your threat model, restrict webhook targets
  at the network/firewall layer as well.

## Secret storage and rotation

- **Passwords**: Argon2id hash only, never recoverable.
- **Session tokens**: SHA-256 hash only, never recoverable (see "Sessions"
  above).
- **Webhook signing secrets**: the one secret Threnda stores *reversibly* —
  it must be recoverable to HMAC-sign each outgoing delivery. Encrypted at
  rest with Fernet (`threnda.app.services.crypto`, from the `cryptography`
  package) using a key derived from `THRENDA_SECRET_KEY`. It is shown to the
  admin exactly once, at creation, and never included in any subsequent API
  response, page render, or log line.
- **Rotating `THRENDA_SECRET_KEY`** invalidates every previously stored
  webhook secret (they become undecryptable) but does **not** invalidate
  existing sessions (session validity depends only on `UserSession.token_hash`
  in the database, not on `THRENDA_SECRET_KEY`) or break CSRF verification
  (it is recomputed fresh, with the current key, on both sides of every
  request). After rotating the key, re-create any webhook endpoints.

## Fail-closed startup checks

`threnda.app.core.startup_checks.validate_production_readiness`, run from
`create_app()` before the database engine is even built, refuses to start
with `THRENDA_ENV=production` if: `THRENDA_SECRET_KEY` is still the
placeholder `change-me`, is shorter than 32 characters, or
`THRENDA_ALLOW_INSECURE_COOKIES` is set. Development stays permissive so a
fresh clone works with the shipped `.env.example` defaults.

## What this is not

- Not SSO/SAML/OIDC/SCIM — Enterprise-only, not implemented here (see
  CLAUDE.md, "Community / Enterprise boundary").
- Not multi-factor authentication.
- Not an independent third-party security audit.
- Not hardened against a malicious co-located process on the same host —
  Threnda assumes normal OS-level file/process isolation, the same as any
  self-hosted application.

## Release security verification

The v0.1.0 release-candidate gate includes `pip-audit`, a high-severity Bandit
scan, the full test suite and a fixable high/critical vulnerability scan of the
final container image. This is automated evidence about the checked revision,
not a substitute for an independent penetration test or source review. The
Local AI runtime and model are revision-pinned, size-checked and SHA-256 checked
before installation; the runtime listens on loopback only, and all model output
remains untrusted advisory data subject to schema validation and human review.
