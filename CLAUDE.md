# CLAUDE.md — Threnda

Guidance for Claude Code sessions working in this repository.

## Product

- **Name:** Threnda
- **Edition in this repository:** Threnda Community
- **Tagline:** Every finding. One queue.
- **What it is:** an open-source, self-hosted security finding management and
  exposure management platform.
- **Status:** early development / pre-alpha. Do not describe unimplemented
  behaviour as if it works, in code comments, docs, commit messages or the UI.

## Clean-room rules (permanent, non-negotiable)

Threnda is an **independently developed clean-room project**.

Permitted sources of information:

- public documentation
- public standards
- open-source information
- synthetic test data
- code authored specifically for Threnda

Never use or reproduce:

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

If a task appears to require any of the above, stop and say so instead of
improvising something that resembles it.

Related working rules:

- **Never fabricate vendor APIs.** If a vendor's API shape is not in public
  documentation you have actually read, do not guess it.
- **Never assume proprietary API responses.** Do not invent response payloads
  from memory of a non-public system.
- **Use official public documentation** when building future integrations.
- **Use synthetic fixture data** for every test and every demo dataset. No
  real hostnames, IPs, customer names, or real findings — ever, including in
  `demo-data/`.
- **Write tests before considering a feature complete.**
- **Keep Community genuinely useful.** Community is a real product, not a
  crippled trial.
- **Do not introduce artificial asset or finding limits.**

## Architecture principles

Start as a **modular monolith**. Split things out only when there is a
concrete, demonstrated reason.

- Python 3.12+
- FastAPI
- SQLAlchemy 2
- Pydantic v2
- Alembic for migrations
- SQLite by default
- PostgreSQL-compatible architecture (no SQLite-only constructs in the schema)
- Jinja2 + HTMX frontend
- Minimal JavaScript
- Docker-first
- No Kubernetes requirement for Community
- Optional local AI only
- No external LLM requirement
- No telemetry by default

### Do not introduce without strong justification

Kafka · Elasticsearch · RabbitMQ · Neo4j · Redis · Kubernetes ·
microservice architecture · a separate vector database

"It would scale better later" is not strong justification. Bring a concrete
problem that the current stack has actually failed to solve.

### Target development machine

An Apple Silicon MacBook with **16 GB RAM**. The whole application — API,
database, worker, frontend — must run comfortably there alongside an editor
and a browser. Keep it lightweight. A dependency that pulls in a JVM, a
multi-gigabyte model, or a multi-container orchestration requirement is a
design failure for Community.

## Community / Enterprise boundary

This is the intended high-level split. Nothing here is implemented yet.

**Threnda Community will eventually include:**

- persistent local database
- CSV ingestion
- JSON ingestion
- SARIF ingestion
- pentest report ingestion
- common finding model
- exposure model
- finding history
- basic correlation
- deduplication
- deterministic risk scoring
- remediation workflow
- manual ownership
- local accounts
- local optional AI
- REST API
- CSV/JSON export

**Future commercial (Enterprise) functionality may include:**

Live vendor integrations — Qualys, Red Hat ACS, Rapid7, Pentera, Tenable,
Wiz, Snyk, ServiceNow, advanced Jira, Splunk.

Platform capabilities — advanced correlation, security knowledge graph,
attack-path analysis, automated ownership, CMDB enrichment, SSO, SAML, OIDC,
SCIM, advanced RBAC, enterprise auditing, HA, BYOC, AWS/Azure/GCP Terraform
deployment, OpenShift/Kubernetes enterprise deployment, air-gapped update
management, advanced private AI orchestration, SLA and premium support.

Enterprise functionality lives in the separate private
`Parzival1019/threnda-enterprise` repository. **Do not implement it here**,
and do not copy Community source code into that repository.

## Repository layout

```
threnda/
├── .github/
│   ├── ISSUE_TEMPLATE/
│   └── workflows/
├── docs/
├── demo-data/        synthetic datasets only
├── tests/
├── threnda/          application package
├── .env.example      placeholders only, never real secrets
├── .gitignore
├── CLAUDE.md
├── CONTRIBUTING.md
├── SECURITY.md
├── README.md
└── pyproject.toml
```

## Licensing

No license has been chosen. Do not add a `LICENSE` file, and do not describe
the project as MIT, Apache, GPL or anything else until the owner decides.

## Current state

Bootstrap only. Milestone 1 has not started. Do not begin implementing the
application unless explicitly asked to.
