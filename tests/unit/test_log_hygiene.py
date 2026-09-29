"""SC-006: document content never reaches logs, spans or error messages."""

import io
import json
import logging
from typing import Any

import structlog
from pydantic import BaseModel

from contacompa.application.pipeline import extract
from contacompa.domain.config import RunConfig
from contacompa.domain.prompt import Prompt
from contacompa.infrastructure.observability.cost import Usage
from contacompa.infrastructure.observability.logging import configure_logging, get_logger
from contacompa.infrastructure.parsing.base import ParsedDoc
from contacompa.infrastructure.providers.base import InvalidOutputError, ProviderResult
from tests.conftest import make_invoice_pdf

SENTINEL = "SENTINEL-ULTRA-SECRET-VENDOR-9931"


class LeakyProvider:
    name = "leaky"

    async def extract(
        self, document: ParsedDoc, schema: type[BaseModel], prompt: Prompt, cfg: RunConfig
    ) -> ProviderResult:
        return ProviderResult(
            model_output={"doc_type": "invoice"},
            usage=Usage(input_tokens=1, output_tokens=1),
            latency_ms=1,
        )


async def test_document_content_never_logged() -> None:
    configure_logging()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    handler.setFormatter(root.handlers[0].formatter)
    root.addHandler(handler)
    try:
        pdf = make_invoice_pdf(vendor=SENTINEL)
        cfg = RunConfig(provider="gemini", model="gemini-2.5-flash", parser="pymupdf")
        error: str = ""
        try:
            await extract(pdf, cfg, LeakyProvider())
        except InvalidOutputError as exc:
            error = str(exc)
        get_logger("test").info("after", stage="done")
    finally:
        root.removeHandler(handler)
        structlog.contextvars.clear_contextvars()
    logged = stream.getvalue()
    assert SENTINEL not in logged
    assert SENTINEL not in error
    lines: list[dict[str, Any]] = [json.loads(line) for line in logged.splitlines() if line.strip()]
    assert any(entry.get("event") == "after" for entry in lines)
