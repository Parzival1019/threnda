# Threnda Community v0.1.0

**Every finding. One queue.**

The first Community release turns scanner exports and pentest reports into a
private, self-hosted security finding queue. It includes file-based ingestion,
deduplication, import differentials, exposure workflow, explainable risk
scoring, authentication and a lightweight web interface.

Local AI is built in: Threnda manages a checksum-pinned llama.cpp runtime and
Qwen2.5 1.5B Instruct Q4_K_M model. Users do not install Ollama or create a
cloud account. AI output remains advisory and requires human review.

## Install

Follow the Docker quick start in the README. Before exposing an instance beyond
localhost, configure a reverse proxy with HTTPS, supply strong production
secrets and read `docs/deployment.md` and `docs/security.md`.

## Scope and limitations

This is an early Community release, not an independently audited enterprise
security product. It has no OCR, MFA, password-reset email flow, high
availability, live scanner connectors or Windows Local AI package. See the
roadmap and security documentation for the exact boundary.
