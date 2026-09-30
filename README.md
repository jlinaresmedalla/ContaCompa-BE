# Contacompa API

Turns Peruvian purchase documents (PDFs and phone photos) into validated purchase records, with measured accuracy and cost per document.

[![CI](https://github.com/jlinaresmedalla/ContaCompa/actions/workflows/ci.yml/badge.svg)](https://github.com/jlinaresmedalla/ContaCompa/actions/workflows/ci.yml)

<!-- demo GIF placeholder: assets/demo.gif, record it after the first live run -->

## Overview

A company receives purchase documents from its suppliers and its accountant types each one into a spreadsheet by hand. Contacompa reads each document with an LLM (Gemini), checks the result against Peruvian tax rules (RUC check digit, buyer, IGV and totals), and flags only the records that need a look. The accountant corrects what is flagged and exports to Excel. Extraction runs in a background worker on a PostgreSQL job queue, every model call is traced and priced, and a circuit breaker pauses work during provider outages. Access is by short-lived company API keys.

The React dashboard lives in [Contacompa-frontend](https://github.com/jlinaresmedalla/ContaCompa-FE); its public Home page at [contacompa-fe.pages.dev](https://contacompa-fe.pages.dev) explains the product and links here.

## Architecture

![Contacompa architecture](assets/architecture.png)

The dashboard's Home page draws an interactive version of this diagram from a typed copy of `docs/architecture/architecture.archify.json`; a change to the diagram also updates that frontend copy (spec 005).

## Tech stack

<p align="center">
  <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/python/python-original.svg" alt="Python" title="Python" width="40" height="40"/>
  <img src="https://cdn.simpleicons.org/uv" alt="uv" title="uv" width="40" height="40"/>
  <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/fastapi/fastapi-original.svg" alt="FastAPI" title="FastAPI" width="40" height="40"/>
  <img src="https://cdn.simpleicons.org/pydantic" alt="Pydantic" title="Pydantic" width="40" height="40"/>
  <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/postgresql/postgresql-original.svg" alt="PostgreSQL" title="PostgreSQL" width="40" height="40"/>
  <img src="https://cdn.simpleicons.org/sqlalchemy" alt="SQLAlchemy" title="SQLAlchemy" width="40" height="40"/>
  <img src="https://cdn.simpleicons.org/googlegemini" alt="Gemini" title="Gemini" width="40" height="40"/>
  <img src="https://cdn.simpleicons.org/langchain" alt="LangSmith" title="LangSmith" width="40" height="40"/>
  <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/opentelemetry/opentelemetry-original.svg" alt="OpenTelemetry" title="OpenTelemetry" width="40" height="40"/>
  <img src="https://cdn.simpleicons.org/ruff" alt="Ruff" title="Ruff" width="40" height="40"/>
  <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/pytest/pytest-original.svg" alt="pytest" title="pytest" width="40" height="40"/>
  <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/docker/docker-original.svg" alt="Docker" title="Docker" width="40" height="40"/>
  <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/githubactions/githubactions-original.svg" alt="GitHub Actions" title="GitHub Actions" width="40" height="40"/>
  <img src="https://cdn.simpleicons.org/render/000000/ffffff" alt="Render" title="Render" width="40" height="40"/>
  <img src="https://cdn.simpleicons.org/neon" alt="Neon" title="Neon" width="40" height="40"/>
</p>

Also Alembic, structlog and mypy strict.

## Project structure

```text
src/contacompa/
├── entrypoints/      # API routes, worker loop, CLI
├── application/      # services (use cases, own the transaction) and the extraction pipeline
├── domain/           # pure rules: schema, checks, RUC, IGV; imports nothing
├── infrastructure/   # database and job queue, file store, LLM providers, parsing, observability
└── config.py         # every setting and its default
tests/
├── unit/
└── integration/      # real PostgreSQL
```

## Environment variables

Create a `.env` file in the repository root:

```dotenv
DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/doc_extraction
# Secret used to mint company API keys
ADMIN_API_KEY=change-me
# Folder for uploaded files
BLOB_DIR=./data/local/blobs
# Gemini key for extraction
GOOGLE_API_KEY=your-gemini-key
```

Every other setting and its default is in `src/contacompa/config.py`. Never commit `.env`.

## Getting started

Requires Python 3.13, uv, Docker and a Gemini API key.

```bash
uv sync --frozen
docker compose up -d db
uv run alembic upgrade head
uv run uvicorn contacompa.entrypoints.api.app:create_app --factory --reload
uv run python -m contacompa.entrypoints.worker   # in another terminal
```

Swagger runs at `http://localhost:8000/docs`, where you mint a company key with `POST /v1/api-keys` (header `X-Admin-Key`) and use it as `X-API-Key`.

## Quality checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest                  # unit
uv run pytest -m integration   # needs Docker
```

## Deployment

One Docker image on Render runs migrations, then the API and the worker as two processes. Postgres and file storage are on Neon, the dashboard on Cloudflare Pages, all on free plans. `render.yaml` defines the service; secrets live in the Render dashboard.
