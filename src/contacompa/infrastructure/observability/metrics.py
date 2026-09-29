"""OpenTelemetry metric instruments for contacompa.

Instrument names and units are fixed contracts (ADR 0003). `get_instruments()` lazily creates
and caches the `Instruments` bundle so callers everywhere get the same instrument objects.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import (
    CallbackOptions,
    Counter,
    Histogram,
    MeterProvider,
    ObservableGauge,
    Observation,
    get_meter,
    set_meter_provider,
)
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

_METER_NAME = "contacompa"


@dataclass
class QueueStats:
    """Latest known job-queue depth and age, updated by the worker."""

    queued: float = 0.0
    oldest_age_seconds: float = 0.0


_queue_stats = QueueStats()


def set_queue_stats(queued: int, oldest_age_seconds: float) -> None:
    """Update the queue depth/age the observable gauges report."""
    _queue_stats.queued = float(queued)
    _queue_stats.oldest_age_seconds = oldest_age_seconds


def queued_callback(options: CallbackOptions) -> Iterable[Observation]:
    """Observable-gauge callback yielding the current queue depth."""
    yield Observation(_queue_stats.queued)


def oldest_age_callback(options: CallbackOptions) -> Iterable[Observation]:
    """Observable-gauge callback yielding the current oldest-job age in seconds."""
    yield Observation(_queue_stats.oldest_age_seconds)


def configure_metrics(service_name: str, otlp_endpoint: str | None) -> MeterProvider:
    """Install a global MeterProvider for `service_name`.

    When `otlp_endpoint` is None, the provider is installed with no reader (offline/tests).
    Otherwise metrics are periodically exported over OTLP/HTTP to
    `{otlp_endpoint}/v1/metrics`.
    """
    resource = Resource.create({"service.name": service_name})
    readers: list[MetricReader] = []
    if otlp_endpoint is not None:
        exporter = OTLPMetricExporter(endpoint=f"{otlp_endpoint}/v1/metrics")
        readers.append(PeriodicExportingMetricReader(exporter))
    provider = SdkMeterProvider(resource=resource, metric_readers=readers)
    set_meter_provider(provider)
    return provider


@dataclass
class Instruments:
    """Bundle of every metric instrument this service emits."""

    http_request_duration_seconds: Histogram
    job_duration_seconds: Histogram
    job_turnaround_seconds: Histogram
    llm_tokens_total: Counter
    llm_cost_usd_total: Counter
    llm_latency_seconds: Histogram
    jobs_queued: ObservableGauge
    jobs_oldest_age_seconds: ObservableGauge
    jobs_failed_total: Counter


@lru_cache
def get_instruments() -> Instruments:
    """Create (once) and return the process-wide `Instruments` bundle."""
    meter = get_meter(_METER_NAME)
    return Instruments(
        http_request_duration_seconds=meter.create_histogram(
            "http_request_duration_seconds", unit="s"
        ),
        job_duration_seconds=meter.create_histogram("job_duration_seconds", unit="s"),
        job_turnaround_seconds=meter.create_histogram("job_turnaround_seconds", unit="s"),
        llm_tokens_total=meter.create_counter("llm_tokens_total"),
        llm_cost_usd_total=meter.create_counter("llm_cost_usd_total"),
        llm_latency_seconds=meter.create_histogram("llm_latency_seconds"),
        jobs_queued=meter.create_observable_gauge("jobs_queued", callbacks=[queued_callback]),
        jobs_oldest_age_seconds=meter.create_observable_gauge(
            "jobs_oldest_age_seconds", callbacks=[oldest_age_callback]
        ),
        jobs_failed_total=meter.create_counter("jobs_failed_total"),
    )
