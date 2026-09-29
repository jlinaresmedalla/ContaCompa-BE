from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from contacompa.config import Settings, get_settings
from contacompa.entrypoints.api import middleware
from contacompa.entrypoints.api.routes import (
    api_keys,
    documents,
    exports,
    jobs,
    me,
    monitor,
    ops,
    purchase_docs,
)
from contacompa.infrastructure.blob import make_blob_store
from contacompa.infrastructure.db.engine import make_engine, make_session_factory
from contacompa.infrastructure.db.repos import ensure_company
from contacompa.infrastructure.observability.llm_tracing import configure_langsmith
from contacompa.infrastructure.observability.logging import configure_logging
from contacompa.infrastructure.observability.metrics import configure_metrics
from contacompa.infrastructure.observability.tracing import (
    configure_tracing,
    instrument_app,
    instrument_httpx,
    instrument_sqlalchemy,
)


def _version() -> str:
    try:
        return version("contacompa")
    except PackageNotFoundError:
        return "0.0.0"


def create_app(settings: Settings | None = None, *, telemetry: bool = True) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        if telemetry:
            configure_tracing(settings.otel_service_name, settings.otel_exporter_otlp_endpoint)
            configure_metrics(settings.otel_service_name, settings.otel_exporter_otlp_endpoint)
            instrument_httpx()
            configure_langsmith(settings)
        engine = make_engine(settings.database_url)
        if telemetry:
            instrument_sqlalchemy(engine)
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessions = make_session_factory(engine)
        app.state.blobs = make_blob_store(settings)
        async with app.state.sessions() as session:
            await ensure_company(
                session,
                ruc=settings.company_ruc,
                legal_name=settings.company_name,
            )
            await session.commit()
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="contacompa", version=_version(), lifespan=lifespan)
    middleware.install(app)

    # The dashboard (web/) runs on another origin and sends X-API-Key from the browser.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "X-Purchase-Doc-Count"],
    )
    app.include_router(ops.router)
    app.include_router(api_keys.router)
    app.include_router(me.router)
    app.include_router(documents.router)
    app.include_router(jobs.router)
    app.include_router(purchase_docs.router)
    app.include_router(purchase_docs.reports)
    app.include_router(exports.router)
    app.include_router(monitor.router)
    if telemetry:
        instrument_app(app)
    return app
