"""Unit tests for structlog JSON logging with OpenTelemetry trace-id enrichment."""

import io
import json
import logging

from opentelemetry.sdk.trace import TracerProvider

from contacompa.infrastructure.observability.logging import configure_logging, get_logger


def _capture_root_handler_output() -> io.StringIO:
    """Redirect the root logger's (only) handler to an in-memory buffer and return it."""
    stream = io.StringIO()
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.stream = stream
    return stream


def test_configure_logging_writes_one_json_line() -> None:
    """A single log call renders as one JSON line with event, level, extra kwarg, timestamp."""
    configure_logging()
    stream = _capture_root_handler_output()

    get_logger("t").info("hello", k=1)

    lines = [line for line in stream.getvalue().splitlines() if line]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "hello"
    assert payload["k"] == 1
    assert payload["level"] == "info"
    assert "timestamp" in payload


def test_configure_logging_adds_trace_context_inside_a_span() -> None:
    """Inside an active span, the JSON line carries a 32-hex-char trace_id."""
    configure_logging()
    stream = _capture_root_handler_output()

    provider = TracerProvider()
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("x"):
        get_logger("t").info("inside-span")

    lines = [line for line in stream.getvalue().splitlines() if line]
    payload = json.loads(lines[-1])
    assert len(payload["trace_id"]) == 32
    int(payload["trace_id"], 16)  # raises ValueError if not valid hex


def test_configure_logging_outside_a_span_has_no_trace_id() -> None:
    """Without an active span, no trace_id is added."""
    configure_logging()
    stream = _capture_root_handler_output()

    get_logger("t").info("no-span")

    payload = json.loads(stream.getvalue().splitlines()[-1])
    assert "trace_id" not in payload


def test_configure_logging_twice_leaves_one_handler() -> None:
    """Calling configure_logging twice does not duplicate handlers on the root logger."""
    configure_logging()
    configure_logging()

    assert len(logging.getLogger().handlers) == 1
