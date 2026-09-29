"""OpenTelemetry tracing setup: provider, exporter, and instrumentation helpers.

FastAPI is not a dependency of this task, so the `FastAPI` type is imported only under
`TYPE_CHECKING`, and `FastAPIInstrumentor` is imported lazily inside `instrument_app` so
importing this module never requires fastapi to be installed.
"""

from typing import TYPE_CHECKING

from opentelemetry import trace as trace_api
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncEngine


def configure_tracing(service_name: str, otlp_endpoint: str | None) -> TracerProvider:
    """Install a global TracerProvider for `service_name`.

    When `otlp_endpoint` is None, the provider is installed with no exporter (offline/tests).
    Otherwise spans are batch-exported over OTLP/HTTP to `{otlp_endpoint}/v1/traces`.
    """
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    if otlp_endpoint is not None:
        exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace_api.set_tracer_provider(provider)
    return provider


def instrument_app(app: "FastAPI") -> None:
    """Instrument a FastAPI app with OpenTelemetry (ASGI request spans)."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)


def instrument_httpx() -> None:
    """Instrument the httpx client library so outbound calls get spans."""
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()


def instrument_sqlalchemy(engine: "AsyncEngine") -> None:
    """Instrument a SQLAlchemy async engine so queries get spans."""
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)


def get_tracer(name: str) -> Tracer:
    """Return a tracer for `name` from the currently installed global provider."""
    return trace_api.get_tracer(name)
