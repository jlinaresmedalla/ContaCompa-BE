# Contacompa API

FastAPI service for Peruvian purchase documents: PDF/photo intake, a PostgreSQL job queue, Gemini extraction, normalized purchase records, observations, corrections, original-file retrieval, Excel export, costs, and structured logs with LLM tracing. The React dashboard is in the separate `Contacompa-frontend` repository. Every environment works on real documents behind API-key sign-in; there is no demo mode (ADR 0014).

## Current state

The API, worker, Gemini adapter, corrections and export are implemented. Accuracy numbers come only from the hand-checked nine-document private set, which is not complete yet. The hosted deployment (sign-in, short-lived keys, Neon Object Storage for files) is specified in spec 002.

## Architecture

```text
src/contacompa/
  entrypoints/     api/ (FastAPI), worker.py, cli/        ← React dashboard calls api/ over HTTPS
  application/     services/ (use cases, own the transaction), pipeline/ (parse → Gemini → verify)
  domain/          pure rules: schema, checks, RUC, IGV   ← imports nothing
  infrastructure/  db/ (Postgres, job queue, migrations), blob.py, providers/, parsing/, observability/
```

One package, one container image; the command picks the role (API, worker or CLI). Dependencies point inward: entrypoints call application services, services use the domain and infrastructure, and the domain imports nothing (ADR 0013). The API stores a file and job in one transaction. The worker claims jobs with `FOR UPDATE SKIP LOCKED`, validates model output, and materializes records. When the model provider is down, a circuit breaker pauses claiming and requeues affected jobs without spending attempts (ADR 0021); `GET /v1/monitor` reports its state. Supplier RUC is shared identity; the printed supplier name is stored on each purchase record. Corrections are logged and can be exported without an approval step.

## Local setup

Requires Python 3.13, `uv`, Docker, and a Gemini key for live extraction.

```bash
# Create .env with DATABASE_URL, ADMIN_API_KEY, BLOB_DIR and GOOGLE_API_KEY;
# every setting and its default is in src/contacompa/config.py
# (optional S3_BUCKET + AWS_* variables send files to a bucket instead of BLOB_DIR)
uv sync --frozen
docker compose up -d db
uv run alembic upgrade head
uv run uvicorn contacompa.entrypoints.api.app:create_app --factory --reload
# In another terminal:
uv run python -m contacompa.entrypoints.worker
```

API: `http://localhost:8000/docs`. The dashboard repository uses `VITE_API_URL=http://localhost:8000`; sign in there with an API key (12 hours by default, up to a week). Mint one with `POST /v1/api-keys`, authorized by the `X-Admin-Key` header (`ADMIN_API_KEY`), for the company seeded from `COMPANY_RUC` and `COMPANY_NAME`; `GET /v1/me` shows the company and the key's expiry. `ADMIN_API_KEY` is a secret; if it is unset, minting is disabled (401). `docker compose up --build` is an alternative for the API and worker. The API sniffs PDFs, JPEGs, and PNGs; `MAX_UPLOAD_BYTES` and `MAX_PAGES` configure limits.

File storage: uploads go to `BLOB_DIR` on disk by default. Set `S3_BUCKET` and the standard AWS variables (`AWS_ENDPOINT_URL_S3`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) to store them in an S3-compatible bucket such as Neon Object Storage (path-style addressing, private bucket, files served only through the API); `BLOB_DIR` is then not needed. The API and the worker must use the same store.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest                     # run by the repository owner
uv run pytest -m integration      # run by the repository owner, needs Docker
```

Accuracy scoring runs on the private set once its labels and predictions are reviewed: `uv run python -m contacompa.entrypoints.cli.evaluate --private --set <private-path>`. Private inputs and labels are ignored by Git.

## Deployment

**Live:** dashboard `https://contacompa-fe.pages.dev` · API `https://contacompa.onrender.com` (Swagger at `https://contacompa.onrender.com/docs`). Sign-in needs an API key (12 hours by default, up to a week) that the owner mints; there is no public view and no demo mode.

![Demo: sign in, upload, extraction, corrections](assets/demo.gif) <!-- demo GIF placeholder: record it after the first live run -->

The hosted stack runs on free plans with no payment method:

```text
Cloudflare Pages (React dashboard)
        │ HTTPS, X-API-Key
        ▼
Render Free, Ohio: one container, `deploy/start.sh`
  alembic upgrade head → worker + API (two processes; if one dies the container exits and restarts)
        ├──► Neon Postgres (AWS US East 2)
        ├──► Neon Object Storage, private bucket `documents` (files served only through the API)
        └──► Gemini free tier, LangSmith traces
```

`render.yaml` defines the Render service (Docker, `contacompa-api`, health check `/readyz`); secrets (`DATABASE_URL`, `ADMIN_API_KEY`, `GOOGLE_API_KEY`, `LANGSMITH_API_KEY`, `AWS_*`, `CORS_ORIGINS`) are set in the Render dashboard, never committed. The image ships `deploy/start.sh` for the hosted command; it is also the Dockerfile default; local `docker compose` sets its own command per service (migrate, API, worker). Render Free sleeps after about 15 minutes idle, so the first request after a pause is slow. Step-by-step setup and the smoke check are in the owner's `docs/deploy.md` (local-only, like the rest of `docs/`); the essentials are: create the Neon project (Postgres plus the `documents` bucket), apply `render.yaml` as a Blueprint and fill the secrets, deploy the dashboard on Pages with `VITE_API_URL=https://contacompa.onrender.com`, set `CORS_ORIGINS=["https://contacompa-fe.pages.dev"]`, then mint a key at `https://contacompa.onrender.com/docs`.
