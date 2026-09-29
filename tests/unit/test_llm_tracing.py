from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel

from contacompa.config import Settings
from contacompa.domain.config import RunConfig
from contacompa.domain.prompt import Prompt
from contacompa.infrastructure.observability.cost import Usage
from contacompa.infrastructure.observability.llm_tracing import (
    configure_langsmith,
    llm_inputs,
    llm_outputs,
)
from contacompa.infrastructure.parsing.base import Page, ParsedDoc
from contacompa.infrastructure.providers.base import ProviderResult


class PurchaseDoc(BaseModel):
    total: str


def settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql+asyncpg://x/y",
        "api_key": "k",
        "blob_dir": "/tmp/b",  # noqa: S108
        "daily_cost_ceiling_usd": Decimal("1"),
    }
    return Settings(_env_file=None, **{**base, **overrides})


def test_inputs_drop_file_bytes_and_keep_what_explains_the_call() -> None:
    doc = ParsedDoc(data=b"%PDF-secret-bytes", pages=(Page(1, "TOTAL 10"),), parser="pymupdf")
    prompt = Prompt(name="purchase_doc", version="v1", system="sys", user="{document_text}")
    cfg = RunConfig(provider="gemini", model="gemini-3.5-flash-lite")
    traced = llm_inputs(
        {"self": object(), "document": doc, "schema": PurchaseDoc, "prompt": prompt, "cfg": cfg}
    )
    assert traced["model"] == "gemini-3.5-flash-lite"
    assert traced["prompt"] == "purchase_doc_v1" and traced["schema"] == "PurchaseDoc"
    assert traced["document_text"] == "TOTAL 10"
    assert b"secret" not in repr(traced).encode()


def test_outputs_report_usage_in_langsmith_shape() -> None:
    result = ProviderResult(
        model_output={"total": "10"},
        usage=Usage(input_tokens=100, output_tokens=20, cached_input_tokens=5),
        latency_ms=50,
        finish_reason="STOP",
    )
    out = llm_outputs(result)
    assert out["output"] == {"total": "10"}
    assert out["usage_metadata"]["total_tokens"] == 120
    assert out["usage_metadata"]["input_token_details"]["cache_read"] == 5


def test_tracing_needs_flag_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT"):
        monkeypatch.setenv(var, "")
    assert configure_langsmith(settings(langsmith_tracing=True)) is False
    assert configure_langsmith(settings(langsmith_tracing=False, langsmith_api_key="x")) is False
    assert configure_langsmith(settings(langsmith_tracing=True, langsmith_api_key="x")) is True
