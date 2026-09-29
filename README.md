# Contacompa API

FastAPI service for Peruvian purchase documents: PDF/photo intake, a PostgreSQL job queue, Gemini extraction, normalized purchase records, observations, corrections, original-file retrieval, Excel export, costs, and structured logs with LLM tracing. The React dashboard is in the separate `Contacompa-frontend` repository. Every environment works on real documents behind API-key sign-in; there is no demo mode (ADR 0014).

## Current state

The API, worker, Gemini adapter, corrections and export are implemented. Accuracy numbers come only from the hand-checked nine-document private set, which is not complete yet. The hosted deployment (sign-in, short-lived keys, R2 files) is specified in spec 002.

## Architecture

```text
src/contacompa/
  entrypoints/     api/ (FastAPI), worker.py, cli/        ← React dashboard calls api/ over HTTPS
  application/     services/ (use cases, own the transaction), pipeline/ (parse → Gemini → verify)
  domain/          pure rules: schema, checks, RUC, IGV   ← imports nothing
  infrastructure/  db/ (Postgres, job queue, migrations), blob.py, providers/, parsing/, observability/
```

One package, one container image; the command picks the role (API, worker or CLI). Dependencies point inward: entrypoints call application services, services use the domain and infrastructure, and the domain imports nothing (ADR 0013). The API stores a file and job in one transaction. The worker claims jobs with `FOR UPDATE SKIP LOCKED`, validates model output, and materializes records. Supplier RUC is shared identity; the printed supplier name is stored on each purchase record. Corrections are logged and can be exported without an approval step.

## Local setup

Requires Python 3.13, `uv`, Docker, and a Gemini key for live extraction.

```bash
# Create .env with DATABASE_URL, API_KEY, BLOB_DIR and GOOGLE_API_KEY;
# every setting and its default is in src/contacompa/config.py
uv sync --frozen
docker compose up -d db
uv run alembic upgrade head
uv run uvicorn contacompa.entrypoints.api.app:create_app --factory --reload
# In another terminal:
uv run python -m contacompa.entrypoints.worker
```

API: `http://localhost:8000/docs`. The dashboard repository uses `VITE_API_URL=http://localhost:8000` and the same `API_KEY`. `docker compose up --build` is an alternative for the API and worker. The API sniffs PDFs, JPEGs, and PNGs; `MAX_UPLOAD_BYTES` and `MAX_PAGES` configure limits.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest                     # run by the repository owner
uv run pytest -m integration      # run by the repository owner, needs Docker
```

Accuracy scoring runs on the private set once its labels and predictions are reviewed: `uv run python -m contacompa.entrypoints.cli.evaluate --private --set <private-path>`. Private inputs and labels are ignored by Git.

## Deployment

Not deployed yet. The hosted setup (Render Free API and worker, Neon Free Postgres and Object Storage, Cloudflare Pages) arrives with spec 002; see `docs/deploy.md`.
