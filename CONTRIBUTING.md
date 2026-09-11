# Contributing to Threnda

Threnda is in **early development / pre-alpha**. The architecture is still
moving, so please open an issue to discuss anything substantial before you
write it.

## Clean-room requirement

This is the one rule with no exceptions.

Threnda is an independently developed **clean-room** project. Every
contribution must be written from scratch for Threnda, using only:

- public documentation
- public standards
- open-source information
- synthetic test data
- code you authored specifically for Threnda

Never contribute, paste, paraphrase or reconstruct:

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

By opening a pull request you confirm that your contribution meets this
requirement and that you have the right to contribute it. If you work
somewhere that builds something similar, be especially careful about schemas
and vendor API details — model them from public documentation, not memory.

## Data in this repository

**Synthetic fixtures only.** Everything in `tests/` and `demo-data/` must be
invented: made-up hostnames, RFC 5737 / RFC 3849 documentation IP ranges,
fictional organisations, fabricated findings. No real scan output, ever, even
your own — and especially not anonymised real output, which usually is not.

Never commit a `.env` file, a database file, an API token or a credential of
any kind. The `.gitignore` is deliberately aggressive about this, but it is a
safety net, not a substitute for checking your diff.

## Tests

Tests are required. A feature is not complete without them.

- new behaviour gets tests that would fail without the change
- bug fixes get a regression test that reproduces the bug first
- run the suite before opening a PR

## Formatting and linting

- Python 3.12+
- format and lint with **ruff** (`ruff format .` and `ruff check .`)
- type hints on public functions; keep **mypy** clean where it is configured
- follow the conventions already in the surrounding code

CI will check these, so run them locally first.

## Pull requests

- **Small and focused.** One concern per PR. A large PR that mixes a refactor
  with a feature will be sent back to be split.
- Explain *why*, not just *what* — the diff already shows what changed.
- Use [Conventional Commits](https://www.conventionalcommits.org/) for commit
  messages (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`).
- Keep the branch up to date with `main`.
- Do not add a `LICENSE` file or license headers — license selection is
  pending and is the owner's decision.

## Architecture expectations

Read [CLAUDE.md](CLAUDE.md) before making structural changes. In short:
modular monolith, FastAPI + SQLAlchemy 2 + Pydantic v2, SQLite by default with
a PostgreSQL-compatible schema, Jinja2 + HTMX with minimal JavaScript,
Docker-first, no Kubernetes requirement, optional local AI only. Kafka,
Elasticsearch, RabbitMQ, Neo4j, Redis, Kubernetes, microservices and vector
databases need strong, concrete justification.

The whole stack must run comfortably on a 16 GB laptop.

## Security vulnerabilities

Do not open a public issue or PR for a security vulnerability. Follow
[SECURITY.md](SECURITY.md) and report it privately first.
