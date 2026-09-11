# Security Policy

## Reporting a vulnerability

**Please do not report security vulnerabilities in public GitHub issues.**
A public issue discloses the problem to everyone, including people who would
use it, before there is a fix.

Instead, use GitHub's **private vulnerability reporting** on this repository:
open the **Security** tab and choose *Report a vulnerability*. That creates a
private security advisory visible only to the maintainers.

If private reporting is not available to you, open a public issue that says
only that you have found a security issue and would like a private channel —
no details — and a maintainer will arrange one.

A dedicated security contact address has not been set up yet. This section
will be updated when one exists; nothing here should be read as implying an
address that does not exist.

### What to include

- what the issue is, and what an attacker could achieve with it
- the steps to reproduce it
- affected version or commit
- anything you know about how widely it applies

Please use only synthetic data in your report. Do not send real findings, real
customer data, credentials, or internal hostnames.

### What to expect

Threnda is in early development and maintained by a very small team, so please
allow for a slower response than a funded project would give. We will confirm
receipt, keep you updated while we work on a fix, and credit you when the fix
ships unless you ask us not to.

Please give us a reasonable opportunity to fix the issue before disclosing it
publicly.

## Supported versions

| Version | Supported |
| ------- | --------- |
| _none yet_ | — |

Threnda is **pre-alpha** and has had no release. There are no supported
versions, and no security fixes are backported. This table will be filled in
at the first release.

## Security philosophy

Threnda holds a concentrated, high-value dataset: it knows where an
organisation is weak. That shapes the design.

- **Privacy-first.** Your findings are yours. Threnda does not send them
  anywhere. There is no telemetry by default.
- **Offline-first.** The platform is designed to run fully offline. No cloud
  service and no external AI provider is required to operate it.
- **Self-hosted.** You control the deployment, the database and the backups.
- **Minimise what we hold.** Store what the product needs to do its job and
  no more.
- **Do not overclaim.** When a result is uncertain, say so rather than assert
  it. This applies to the product's own security posture as much as to its
  findings.
