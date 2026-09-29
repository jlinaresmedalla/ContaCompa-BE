"""structlog JSON logging enriched with the current OpenTelemetry trace/span id."""

import logging
import typing
from collections.abc import MutableMapping
from typing import Any

import structlog
from opentelemetry import trace as trace_api


def add_trace_context(
    logger: logging.Logger | None, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Add trace_id/span_id to the event dict when a valid span is active."""
    span_context = trace_api.get_current_span().get_span_context()
    if span_context.is_valid:
        event_dict["trace_id"] = trace_api.format_trace_id(span_context.trace_id)
        event_dict["span_id"] = trace_api.format_span_id(span_context.span_id)
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog + stdlib logging to emit one JSON line per event.

    Routes stdlib `logging` through the same structlog processors (via
    `structlog.stdlib.ProcessorFormatter`) so third-party library logs are JSON too.
    Idempotent: replaces the root logger's handlers rather than appending, so calling
    this twice never leaves more than one handler installed.
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_trace_context,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog bound logger for `name`."""
    return typing.cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
