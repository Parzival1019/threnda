# Threnda Community Desktop for macOS

Community Desktop is the zero-Docker path for individual use and evaluation.
It runs the same Community application in a native macOS window and keeps the
HTTP service private to the Mac on `127.0.0.1`.

## What is implemented

- One clickable Apple Silicon `.app` containing Python, FastAPI, SQLite and a
  native WebKit window.
- Automatic database creation and migration at launch.
- A first-run owner-account screen; no terminal bootstrap command and no
  invented default password.
- An instance secret generated once and stored in macOS Keychain.
- Application data stored under
  `~/Library/Application Support/Threnda/` with private directory permissions:
  `threnda.db`, Local AI files, logs and WebKit session storage.
- A one-time, in-memory setup token. It is exchanged for a short-lived,
  HttpOnly, SameSite cookie and is never accepted from a non-loopback client.
- Clean shutdown of the private local server when the window closes.

All normal authentication, CSRF protection, workspace roles, audit events,
imports, exports and optional managed Local AI behavior come from the same
Community codebase as Server mode.

## Run from a source checkout

Requires macOS and Python 3.12+:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[desktop]"
threnda-desktop
```

This is intended for contributors. End users should eventually receive the
signed and notarized app instead of installing Python packages.

## Build the Apple Silicon app

```bash
source .venv/bin/activate
pip install -e ".[desktop-build]"
packaging/macos/build-app.sh
open dist/Threnda.app
```

The build uses py2app's standalone mode and deliberately excludes the optional
PostgreSQL driver and development tools. The produced app is SQLite-only;
Server mode remains the PostgreSQL route.

For isolated development smoke tests, set `THRENDA_DESKTOP_DATA_DIR` before
launching the app executable. Normal users should leave it unset.

## Public release gate

The local build is ad-hoc signed so macOS can validate its internal bundle,
but it is **not** ready for public distribution. A release maintainer must:

1. Supply a real product icon.
2. Build on a controlled Apple Silicon macOS runner.
3. Sign the app and every nested executable with a valid Developer ID
   Application identity, Hardened Runtime and a secure timestamp.
4. Create a DMG, submit it to Apple's notary service with `notarytool`, wait
   for acceptance, and staple the ticket.
5. Verify the signature, Gatekeeper assessment and a clean-machine first run.
6. Publish checksums only after the source-code license has been selected.

Developer credentials, signing identities and notary credentials must never be
committed to this repository.

## Desktop versus Server

Desktop is designed for one person's local queue. It still has workspaces and
roles because it uses the shared Community core, but it is not a shared network
service. To collaborate, run Community Server: Docker Compose, PostgreSQL,
local accounts and basic roles are included without artificial finding, asset
or user caps.

Enterprise remains the home for SSO/SAML/OIDC/SCIM, advanced RBAC, high
availability, live commercial integrations, managed air-gapped updates and
premium support. Community Server is not restricted to solo use.
