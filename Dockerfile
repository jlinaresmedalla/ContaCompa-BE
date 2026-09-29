# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY alembic.ini README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.13-slim-bookworm AS runtime
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 APP_GIT_SHA=$GIT_SHA APP_BUILD_TIME=$BUILD_TIME
# /data/blobs exists and belongs to app before the volume mounts on it: Docker copies this
# ownership into a new named volume, otherwise the volume is root-owned and uploads fail.
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app \
    && mkdir -p /data/blobs && chown app:app /data/blobs
COPY --from=builder --chown=app:app /app /app
# Default command: migrations, worker and API in one container (the hosted setup).
COPY --chmod=755 deploy/start.sh /usr/local/bin/start.sh
USER app
WORKDIR /app
EXPOSE 8000
# docker-compose overrides it per service (migrate, api, worker), so local runs never use it.
CMD ["/usr/local/bin/start.sh"]
