# Threnda Community v0.1 — target specification

This document records the agreed product target. It is not a claim that these features exist. Track actual delivery in docs/implementation-plan.md. Implement and verify one milestone at a time.

The owner has now explicitly authorized Community implementation. CLAUDE.md clean-room rules still apply. Cloud-AI prohibitions below concern the application's handling of security data; development with Claude Code is expressly authorized. Use only this repository, public documentation and synthetic fixtures. Do not inspect or import files from other local projects or any employer environment.

# PRODUCT VISION

Threnda turns fragmented security findings into one normalized, deduplicated and actionable security queue.

Community Edition should allow a security engineer to run Threnda locally, import security findings from files, persist them, compare imports over time, deduplicate findings, prioritize them and manage remediation lifecycle.

The Community Edition must be genuinely useful and must NOT be a crippled demo.

Community Edition principles:

- self-hosted
- local-first
- privacy-first
- no required external services
- no required cloud
- no required external LLM
- no telemetry by default
- unlimited findings
- unlimited assets
- unlimited imports
- unlimited local users
- low hardware requirements
- simple setup
- useful on a 16 GB Apple Silicon MacBook

Future Pro/Enterprise live integrations must be architecturally possible without rewriting the Community core.

# CORE USER EXPERIENCE

The ideal first run:

1. User starts Threnda.
2. Opens localhost in browser.
3. Uploads a CSV, JSON, SARIF or pentest report.
4. Threnda parses and normalizes findings.
5. Findings are stored persistently.
6. Duplicate findings are correlated.
7. Risk scores are calculated.
8. User sees one clean actionable finding queue.
9. User can acknowledge a finding, assign an owner, set remediation target date and change status.
10. On later imports Threnda identifies new, resolved, reopened and unchanged findings.

Target:

docker compose up -d

Then:

http://localhost:8080

No manual database configuration should be required for the default Community setup.

# TECHNOLOGY STACK

Prefer boring, maintainable technology.

Backend:
- Python 3.12+
- FastAPI
- SQLAlchemy 2
- Alembic
- Pydantic v2

Default database:
- SQLite

Architecture must also support:
- PostgreSQL via DATABASE_URL

Frontend:
Use a lightweight approach.

Preferred:
- server-rendered templates
- Jinja2
- HTMX
- minimal JavaScript
- simple CSS

Do NOT introduce React/Next.js unless there is a strong architectural reason.

The product should remain easy to build and run on a small machine.

Packaging:
- Docker
- Docker Compose

Testing:
- pytest
- useful unit and integration tests

Code quality:
- Ruff
- typing
- clear modular architecture

# DOMAIN MODEL

Design a vendor-neutral security model.

At minimum implement:

## Workspace

Even Community Edition should have a workspace abstraction for future multi-tenancy.

Fields:
- id
- name
- created_at

## Source

Represents the origin of findings.

Examples:
- CSV import
- SARIF
- Pentest Report
- Trivy
- Nuclei
- Semgrep

Fields:
- id
- name
- source_type
- metadata
- created_at

## ImportRun

Every ingestion must be tracked.

Fields:
- id
- source_id
- filename
- file_hash
- started_at
- completed_at
- status
- total_records
- successful_records
- failed_records

This allows Threnda to compare repeated imports.

## Asset

Generic asset object.

Possible asset types:
- host
- IP
- URL
- application
- repository
- container_image
- container
- Kubernetes workload
- cloud_resource
- unknown

Fields should include:
- id
- canonical_key
- name
- asset_type
- environment
- metadata
- first_seen
- last_seen

## Finding

Normalized finding.

At minimum:
- id
- fingerprint
- source_id
- source_finding_id
- title
- description
- severity
- cvss_score
- cve
- cwe
- asset_id
- evidence
- remediation
- references
- first_seen
- last_seen
- raw_data
- created_at
- updated_at

## Exposure

An Exposure is the correlated user-facing security issue.

Multiple Findings may map to one Exposure.

Fields:
- id
- canonical_fingerprint
- title
- description
- severity
- risk_score
- status
- owner
- acknowledged
- remediation_target_date
- remediation
- first_seen
- last_seen
- resolved_at
- created_at
- updated_at

Exposure to Finding relationship:
many-to-many or one-to-many as appropriate.

Users should mostly interact with Exposures, not raw Findings.

## ExposureHistory

Every meaningful workflow change must be auditable.

Examples:
- status changed
- acknowledged
- owner changed
- remediation date changed
- finding correlated
- reopened
- resolved

Fields:
- id
- exposure_id
- event_type
- previous_value
- new_value
- actor
- timestamp

# FINDING LIFECYCLE

Implement:

- OPEN
- ACKNOWLEDGED
- IN_PROGRESS
- RISK_ACCEPTED
- RESOLVED

Users can:

- acknowledge
- assign owner
- set target remediation date
- change status
- add notes

Do not automatically perform remediation.

# INGESTION ARCHITECTURE

Create a generic importer interface.

Example concept:

Importer
- detect()
- validate()
- parse()
- normalize()

Each importer must output normalized findings.

Create importers for:

1. Generic CSV
2. Generic JSON
3. SARIF
4. Pentest structured JSON

Also design the architecture for future:
- Trivy
- Nuclei
- Semgrep
- Qualys
- RH ACS
- Tenable
- Pentera

But DO NOT implement proprietary live APIs unless explicitly requested later.

# CSV IMPORT

CSV import must support a mapping wizard.

Example uploaded columns:

Vulnerability
Host
Severity
CVSS
CVE
Description
Solution

User should be able to map:

Vulnerability -> title
Host -> asset
Severity -> severity
etc.

Remember mappings for later imports where practical.

Support common severity names:
- informational
- low
- medium
- high
- critical

# SARIF

Implement SARIF 2.1.0 ingestion.

Extract useful fields such as:
- rule ID
- message
- severity
- file/location
- fingerprints where available
- remediation/help links

Do not make SARIF assumptions tied to a single scanner.

# PENTEST REPORT INGESTION

Community v1 should support:

A. structured pentest JSON import

B. PDF/text ingestion architecture

PDF parsing should be deterministic first.

Extract text locally.

Do NOT require external OCR or external AI APIs.

The initial implementation may provide:

- text extraction
- section detection
- candidate finding extraction framework

If local AI is not configured, the user must still be able to manually convert candidate sections into findings.

Keep PDF parsing modular so local AI can later enhance extraction.

# LOCAL AI ARCHITECTURE

AI must be OPTIONAL.

The core product must function fully without AI.

Create an abstraction:

AIProvider

Capabilities:
- summarize_finding()
- explain_finding()
- suggest_remediation()
- extract_findings_from_text()
- compare_findings()

Initial providers:

1. DisabledAIProvider
2. OllamaAIProvider

Do not call any cloud LLM.

No OpenAI API.
No Anthropic API.
No Gemini API.

All prompts and processing must remain local.

If Ollama is unavailable, Threnda must continue functioning normally.

Allow model configuration via environment variable.

Example:
THRENDA_AI_PROVIDER=ollama
THRENDA_AI_MODEL=<model-name>

AI results must be clearly marked as machine-generated.

AI must NEVER silently modify security records without user approval.

# DEDUPLICATION

Deduplication is one of the most important features.

Build it in layers.

## Layer 1 deterministic fingerprint

Use normalized combinations where available:

- CVE
- CWE
- asset canonical identity
- URL/path
- scanner rule ID
- repository/file
- container image digest
- title normalization

Avoid fingerprint instability.

## Layer 2 fuzzy matching

Use deterministic text similarity for uncertain cases.

Keep dependencies reasonable.

## Layer 3 optional semantic matching

Only if local AI/embedding capability is enabled.

Do NOT automatically merge uncertain findings.

Confidence levels:

- EXACT
- HIGH
- POSSIBLE

EXACT/HIGH may be automatically correlated according to safe rules.

POSSIBLE duplicates must be shown to the user for review.

The UI should allow:

- Merge
- Keep separate

# IMPORT DIFFERENTIALS

On a repeated source import identify:

- NEW
- UNCHANGED
- UPDATED
- RESOLVED
- REOPENED

Example:

Import 1:
100 findings

Import 2:
10 new
4 resolved
3 changed
86 unchanged

Store this history.

Do not delete historical findings.

# RISK SCORING

Implement explainable rule-based scoring.

Risk score:
0-100

Inputs may include:
- severity
- CVSS
- asset environment
- internet exposure if known
- exploit evidence if known
- finding age
- manual business criticality

Do NOT use AI to produce the authoritative risk score.

Every score must be explainable.

Example UI:

Risk 91

CVSS 9.1        +45
Production      +15
Internet facing +20
Age > 30d       +11

Rules must be configurable in code and designed for future configuration.

# UI

UI philosophy:

Extremely simple.

Primary navigation:

1. Findings
2. Imports
3. Sources
4. Settings

Optional:
Dashboard

Do NOT create dozens of screens.

## Findings page

Columns:

Risk
Severity
Finding
Asset
Sources
Owner
Status
Age
Target date

Features:
- text search
- severity filter
- source filter
- owner filter
- status filter
- risk range
- pagination
- sorting

Clicking opens Exposure detail.

## Exposure detail

Show:

Title
Risk
Severity
Status
Owner
Target remediation date

Why it matters
Affected asset
Evidence
Sources
Remediation
References
Timeline

Actions:

Acknowledge
Assign owner
Set target date
Change status
Add note
Review possible duplicates

Optional AI buttons:

Explain
Suggest remediation

## Imports

Show:

filename
source
timestamp
record count
new
updated
resolved
failed
status

Import detail should show errors clearly.

# DASHBOARD

Keep dashboard minimal.

Useful metrics:

- Open exposures
- Critical
- High
- Overdue
- New this week
- Resolved this week
- Average remediation age

Do NOT spend excessive development time on visualizations.

# API

Expose documented REST endpoints.

At minimum:

GET /api/v1/exposures
GET /api/v1/exposures/{id}
PATCH /api/v1/exposures/{id}

GET /api/v1/imports
POST /api/v1/imports

GET /api/v1/sources

GET /api/v1/assets

GET /api/v1/health

Generate OpenAPI automatically via FastAPI.

# GENERIC WEBHOOK / EXPORT

Community should support:

- CSV export
- JSON export

Design a generic outbound webhook mechanism for future use.

Do not build full Jira integration in the first Community milestone unless it is trivial after the core is finished.

# SECURITY

Even Community must have sane security.

Requirements:

- secure password hashing
- secrets from environment variables
- safe file upload handling
- strict upload size limits
- safe filenames
- content validation
- CSRF protection where applicable
- secure session cookies
- SQL injection protection via ORM
- HTML escaping
- no arbitrary shell execution
- no unsafe deserialization
- no external telemetry by default
- audit meaningful administrative changes
- avoid logging sensitive raw findings unnecessarily

Never embed default production credentials.

# AUTHENTICATION

Community v1 may start with:

- local users
- username/password

Prepare interfaces for future:
- OIDC
- SAML
- SCIM

But do not implement them yet.

# DATA PRIVACY

Threnda Community must work offline.

No security data may leave the host unless the user deliberately configures an outbound integration.

No telemetry by default.

Document this clearly.

# RESOURCE TARGET

Community must run comfortably for development on:

Apple Silicon MacBook
16 GB RAM

Target idle resource consumption should be low.

Do not introduce:
- Kafka
- Elasticsearch
- Redis unless absolutely necessary
- Neo4j
- Kubernetes requirement
- separate vector database

Prefer:
- SQLite
- one FastAPI process
- lightweight workers where required

We can scale architecture later.

# DEMO DATA

Create completely synthetic demo data.

Include:

- generic vulnerability CSV
- SARIF sample
- pentest structured JSON
- repeated import showing resolved/new findings

Never use proprietary or employer data.

# TESTING

Add meaningful tests for:

- normalization
- fingerprint generation
- deduplication
- import comparison
- state transitions
- risk scoring
- CSV mapping
- SARIF parsing
- authorization
- API basics

# DOCUMENTATION

README must contain:

- what Threnda Community is
- screenshots placeholders if needed
- quick start
- Docker instructions
- local Python development instructions
- architecture overview
- supported import formats
- local AI setup
- privacy model
- backup instructions
- upgrade instructions
- project status
- clean-room statement
- contribution guide

Also create:

docs/architecture.md
docs/data-model.md
docs/importers.md
docs/deduplication.md
docs/local-ai.md
docs/security.md
docs/roadmap.md

# BRANDING

Product:
Threnda

Edition:
Community

Possible tagline:

"Every finding. One queue."

Keep branding minimal.

# REPOSITORY STRUCTURE

Use a clean structure similar to:

threnda/
  app/
    api/
    core/
    db/
    models/
    schemas/
    services/
      ingestion/
      correlation/
      risk/
      ai/
    importers/
    templates/
    static/
  tests/
  demo-data/
  docs/
  migrations/
  Dockerfile
  docker-compose.yml
  pyproject.toml
  .env.example
  README.md

You may improve this if needed.

# IMPLEMENTATION STRATEGY

Do NOT attempt to generate the entire application blindly in one pass.

Work milestone-by-milestone.

Milestone 1:
- initialize project
- domain models
- database
- migrations
- basic API
- tests

Milestone 2:
- ingestion framework
- JSON
- CSV
- SARIF
- demo data
- tests

Milestone 3:
- fingerprints
- deduplication
- exposures
- import differential engine
- tests

Milestone 4:
- workflow
- history
- risk scoring
- API

Milestone 5:
- web UI
- findings list
- finding detail
- imports
- filters

Milestone 6:
- pentest ingestion
- optional Ollama integration

Milestone 7:
- authentication
- security hardening
- Docker
- documentation
- final tests

After each milestone:

1. run tests
2. lint
3. fix issues
4. explain what was implemented
5. commit with a clear git commit message if git is available

Do not proceed with knowingly broken tests.

# ENGINEERING BEHAVIOUR

When unsure:
- choose simpler architecture
- prefer deterministic behaviour
- avoid premature distributed systems
- avoid unnecessary abstractions
- keep the Community deployment easy

Do not fabricate vendor API structures.

Do not implement proprietary integrations from memory.

When a future connector requires vendor details, rely on official public documentation.

Maintain backward compatibility where reasonable.

Do not silently remove functionality.

# DEFINITION OF DONE

Threnda Community v0.1 is ready when:

A new user can:

1. clone repository
2. run Docker Compose
3. open browser
4. create/login to local account
5. upload demo CSV/SARIF/JSON
6. see findings normalized
7. see duplicates correlated into exposures
8. search/filter exposures
9. inspect exposure
10. acknowledge it
11. assign an owner
12. set remediation deadline
13. change status
14. upload a newer export
15. see new/resolved/reopened differences
16. export results
17. optionally connect a local Ollama model
18. perform all core operations with no internet connection

The test suite must pass.

The README must allow another developer to reproduce the setup without undocumented steps.


## Delivery constraints

- Work on the dedicated `feat/community-v0.1` branch. Do not push or merge automatically.
- Use the existing `.venv` and avoid modifying global tools or account settings.
- Development servers must bind only to `127.0.0.1`; use port 8080 only if available.
- Do not stop or alter existing Docker containers belonging to other projects.
- Install only necessary development/application dependencies. Do not install Ollama or download models during the initial milestones.
- Implement security constraints incrementally. An unauthenticated milestone is local development only, not a deployable release.
- Do not add a license before the owner chooses one.
- Record commands run, actual test outcomes and outstanding limitations in the implementation plan.

