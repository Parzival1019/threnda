# Threnda

**Every finding. One queue.**

Threnda is an open-source, self-hosted **security finding management and
exposure management** platform. It takes the output of the scanners, testers
and reports you already run, turns them into one consistent finding model, and
puts the result into a single actionable queue that a security team can
actually work through.

> **Status: early development / pre-alpha.**
> Nothing described below is finished. This repository currently contains only
> the project bootstrap. Do not deploy it, and do not treat any part of this
> README as a description of working functionality.

## Vision

```
Security Sources
      ↓
   Ingest
      ↓
 Normalize
      ↓
 Correlate
      ↓
 Deduplicate
      ↓
 Prioritize
      ↓
 One actionable queue
```

Most teams do not have a finding shortage. They have the same finding reported
five times, in four formats, by three tools, with no owner and no agreed
priority. Threnda's goal is to collapse that into one queue.

## Community principles

- **Self-hosted** — you run it, on your own infrastructure
- **Privacy-first** — your findings are your findings
- **Local-first** — the default deployment is a single machine
- **Offline-capable** — no internet connection required to operate
- **Vendor-neutral** — no preferred scanner, no preferred cloud
- **Unlimited findings** — no artificial caps
- **Unlimited assets** — no artificial caps
- **No mandatory cloud services**
- **No mandatory external AI** — local AI is optional, never required
- **No telemetry by default**

## Editions

**Threnda Community** (this repository) is intended to be genuinely useful on
its own, not a demo of a paid product. See [CLAUDE.md](CLAUDE.md) for the
documented Community/Enterprise boundary.

**Threnda Enterprise** is a separate private repository for future commercial
extensions (live vendor integrations, SSO/SAML/SCIM, advanced RBAC, HA,
BYOC and air-gapped deployment, enterprise AI orchestration, premium support
tooling). None of it is implemented yet.

## Clean-room notice

Threnda is an **independently developed clean-room project**.

Development may use only:

- public documentation
- public standards
- open-source information
- synthetic test data
- code authored specifically for Threnda

Development must **never** use or reproduce:

- employer source code
- customer source code
- proprietary schemas
- internal API responses
- internal documentation
- non-public workflows
- credentials
- confidential security findings
- internal hostnames
- real customer data
- proprietary business logic

This rule applies permanently to this repository and to every contribution
made to it.

## License

**TODO: License selection is pending before the first public release.**

No license has been chosen yet. Until a `LICENSE` file exists in this
repository, no open-source license is granted — do not assume MIT, Apache,
GPL or any other license applies.

## Contributing & security

- [CONTRIBUTING.md](CONTRIBUTING.md) — how to contribute, and the clean-room
  requirement that applies to every contribution
- [SECURITY.md](SECURITY.md) — how to report a vulnerability privately
