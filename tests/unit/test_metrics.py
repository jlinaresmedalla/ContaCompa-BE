"""Unit tests for OpenTelemetry metric instruments."""

from collections.abc import Generator
from typing import cast

import pytest
from opentelemetry.metrics import CallbackOptions

from contacompa.infrastructure.observability.metrics import (
    configure_metrics,
    get_instruments,
    oldest_age_callback,
    queued_callback,
    set_queue_stats,
)


@pytest.fixture(autouse=True)
def _clear_instruments_cache() -> Generator[None]:
    """Clear the get_instruments cache so each test builds against a fresh meter."""
    get_instruments.cache_clear()
    yield
    get_instruments.cache_clear()


def test_get_instruments_is_cached() -> None:
    """get_instruments() returns the same object on repeated calls."""
    configure_metrics("svc", None)

    first = get_instruments()
    second = get_instruments()

    assert first is second


def _instrument_name(instrument: object) -> str:
    """Return an instrument's OTel name.

    The opentelemetry-api Instrument ABCs don't declare `.name` in their type surface (only
    the SDK's concrete instances set it, at __init__ time) — this is the single, narrow spot
    that works around the gap instead of a `type: ignore` on every call site below.
    """
    return cast(str, instrument.name)  # type: ignore[attr-defined]


def test_instrument_names_match_the_contract() -> None:
    """Every instrument's OTel name matches the fixed contract exactly."""
    configure_metrics("svc", None)

    inst = get_instruments()

    assert _instrument_name(inst.http_request_duration_seconds) == "http_request_duration_seconds"
    assert _instrument_name(inst.job_duration_seconds) == "job_duration_seconds"
    assert _instrument_name(inst.job_turnaround_seconds) == "job_turnaround_seconds"
    assert _instrument_name(inst.llm_tokens_total) == "llm_tokens_total"
    assert _instrument_name(inst.llm_cost_usd_total) == "llm_cost_usd_total"
    assert _instrument_name(inst.llm_latency_seconds) == "llm_latency_seconds"
    assert _instrument_name(inst.jobs_queued) == "jobs_queued"
    assert _instrument_name(inst.jobs_oldest_age_seconds) == "jobs_oldest_age_seconds"
    assert _instrument_name(inst.jobs_failed_total) == "jobs_failed_total"


def test_set_queue_stats_feeds_the_gauge_callbacks() -> None:
    """set_queue_stats updates the values the observable-gauge callbacks yield."""
    set_queue_stats(3, 12.5)

    queued_observations = list(queued_callback(CallbackOptions()))
    oldest_age_observations = list(oldest_age_callback(CallbackOptions()))

    assert len(queued_observations) == 1
    assert queued_observations[0].value == 3
    assert len(oldest_age_observations) == 1
    assert oldest_age_observations[0].value == 12.5
