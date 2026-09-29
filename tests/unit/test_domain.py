import pytest
from pydantic import BaseModel, ValidationError

from contacompa.application.pipeline.prompts import load_prompt
from contacompa.config import Settings
from contacompa.domain.config import RunConfig
from contacompa.domain.prompt import Prompt
from contacompa.domain.schemas import SCHEMAS, get_schema
from contacompa.infrastructure.observability.cost import Usage
from contacompa.infrastructure.parsing.base import Page, ParsedDoc
from contacompa.infrastructure.providers.base import Provider, ProviderResult
from contacompa.infrastructure.providers.registry import (
    _reset_for_tests,
    get_provider,
    register,
    registered,
)


def test_run_config_is_frozen_and_keyed() -> None:
    cfg = RunConfig(
        provider="gemini", model="gemini-2.5-flash", prompt_version="v1", parser="native"
    )
    assert cfg.key() == "gemini/gemini-2.5-flash/v1/native"
    assert cfg.model_copy(update={"effort": "low"}).key().endswith("/low")
    with pytest.raises(ValidationError):
        cfg.provider = "x"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        RunConfig(provider="", model="m")


def test_parsed_doc_text() -> None:
    doc = ParsedDoc(data=b"%PDF", pages=(Page(1, "a"), Page(2, "b")), parser="pymupdf")
    assert doc.text == "a\fb"
    assert doc.page_count == 2
    assert doc.mime_type == "application/pdf"
    assert ParsedDoc(data=b"%PDF", pages=(Page(1, None),), scanned=True).text is None
    assert ParsedDoc(data=b"%PDF").page_count is None


def test_schema_registry() -> None:
    assert set(SCHEMAS) == {"purchase_doc"}
    spec = get_schema("purchase_doc")
    assert spec.version == "1" and spec.decimal_separator == "."
    assert spec.kinds["total_amount"] == "amount"
    assert spec.kinds["lines.quantity"] == "number" and spec.kinds["lines.unit"] == "unit"
    with pytest.raises(KeyError):
        get_schema("contract")


def test_prompts_load_for_every_schema() -> None:
    for name in SCHEMAS:
        prompt = load_prompt(name, "v1")
        assert prompt.system and "{document_text}" in prompt.user
        assert "TEXT" in prompt.render_user(document_text="TEXT")
    with pytest.raises(FileNotFoundError):
        load_prompt("purchase_doc", "v999")


class FakeProvider:
    name = "fake"

    def __init__(self, output: dict[str, object]) -> None:
        self.output = output
        self.calls = 0

    async def extract(
        self, document: ParsedDoc, schema: type[BaseModel], prompt: Prompt, cfg: RunConfig
    ) -> ProviderResult:
        self.calls += 1
        return ProviderResult(
            model_output=self.output, usage=Usage(input_tokens=10, output_tokens=5), latency_ms=7
        )


@pytest.fixture
def clean_registry() -> None:
    _reset_for_tests()


def test_registry(clean_registry: None, test_settings: Settings) -> None:
    provider: Provider = FakeProvider({})  # structural conformance, checked by mypy too
    register("fake", lambda settings: provider)
    assert registered() == ("fake",)
    assert get_provider("fake", test_settings) is provider
    with pytest.raises(ValueError, match="already registered"):
        register("fake", lambda settings: provider)
    with pytest.raises(KeyError, match="registered: \\['fake'\\]"):
        get_provider("nope", test_settings)
