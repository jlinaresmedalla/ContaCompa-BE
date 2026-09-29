"""LangSmith tracing for model calls: prompt, response, tokens and model per call — the content
that OpenTelemetry spans and logs deliberately leave out. Real documents may be traced; the owner
treats them as public information (FR-012).

LangSmith reads its settings from the environment; `.env` is loaded by pydantic-settings and not
exported, so `configure_langsmith` exports them explicitly at process start."""

import os
from collections.abc import Callable
from typing import Any, TypeVar, cast

from langsmith import get_current_run_tree, traceable

from contacompa.config import Settings

F = TypeVar("F", bound=Callable[..., Any])


def configure_langsmith(settings: Settings) -> bool:
    """Turn tracing on only when enabled and a key is present. Returns whether it is on."""
    key = settings.langsmith_api_key.get_secret_value() if settings.langsmith_api_key else ""
    enabled = settings.langsmith_tracing and bool(key)
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    if enabled:
        os.environ["LANGSMITH_API_KEY"] = key
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    return enabled


def llm_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """What a trace shows as the call's input. PDF bytes are dropped: large and binary."""
    document, prompt, cfg, schema = (inputs.get(k) for k in ("document", "prompt", "cfg", "schema"))
    return {
        "model": getattr(cfg, "model", None),
        "prompt": f"{prompt.name}_{prompt.version}" if prompt is not None else None,
        "system": getattr(prompt, "system", None),
        "schema": getattr(schema, "__name__", None),
        "parser": getattr(document, "parser", None),
        "pages": getattr(document, "page_count", None),
        "document_text": getattr(document, "text", None),
    }


def llm_outputs(result: Any) -> dict[str, Any]:
    """The model's JSON plus token usage in the shape LangSmith uses for its token columns."""
    usage = result.usage
    return {
        "output": result.model_output,
        "finish_reason": result.finish_reason,
        "usage_metadata": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.input_tokens + usage.output_tokens,
            "input_token_details": {"cache_read": usage.cached_input_tokens},
        },
    }


def tag_model(model: str) -> None:
    """Attach the model name to the current run so LangSmith groups and prices by model."""
    run = get_current_run_tree()
    if run is not None:
        run.metadata["ls_model_name"] = model


def trace_llm_call(provider: str) -> Callable[[F], F]:
    """Decorate a provider's `extract(document, schema, prompt, cfg)` as a LangSmith LLM run."""

    def decorate(func: F) -> F:
        traced = traceable(
            run_type="llm",
            name=f"{provider}.extract",
            metadata={"ls_provider": provider},
            process_inputs=llm_inputs,
            process_outputs=llm_outputs,
        )(func)
        return cast(F, traced)

    return decorate
