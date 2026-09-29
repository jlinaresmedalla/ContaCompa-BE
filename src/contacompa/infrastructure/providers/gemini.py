"""Google Gemini adapter (free tier by default).

Google may use free-tier inputs for training; ADR 0005 records which documents are sent here.
"""

import asyncio
import json
import time
from typing import Any

import httpx
from aiolimiter import AsyncLimiter
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential_jitter,
)

from contacompa.config import Settings
from contacompa.domain.config import RunConfig
from contacompa.domain.prompt import Prompt
from contacompa.infrastructure.observability.cost import Usage, cost_for
from contacompa.infrastructure.observability.llm_tracing import tag_model, trace_llm_call
from contacompa.infrastructure.observability.metrics import get_instruments
from contacompa.infrastructure.observability.tracing import get_tracer
from contacompa.infrastructure.parsing.base import ParsedDoc
from contacompa.infrastructure.providers.base import (
    InvalidOutputError,
    ProviderError,
    ProviderResult,
    ProviderUnavailableError,
    RateLimitedError,
)
from contacompa.infrastructure.providers.registry import register

_tracer = get_tracer(__name__)

MAX_RETRY_AFTER_SECONDS = 60.0  # a server asking for a longer wait is treated as an outage
MAX_DEADLINE_SECONDS = 180.0
DEADLINE_LEASE_FRACTION = 0.6  # one job's provider time must stay well below the worker lease


def map_error(exc: Exception) -> ProviderError:
    code = getattr(exc, "code", None)
    if isinstance(exc, genai_errors.APIError):
        if code == 429:
            return RateLimitedError("gemini rate limited", retry_after_seconds=_retry_after(exc))
        if code == 408 or (code is not None and code >= 500):
            return ProviderUnavailableError(f"gemini server error {code}")
        return ProviderError(f"gemini request failed ({code})")
    if isinstance(exc, TimeoutError | ConnectionError | httpx.TransportError):
        return ProviderUnavailableError(f"gemini unreachable: {type(exc).__name__}")
    return ProviderError(f"gemini failure: {type(exc).__name__}")


def _retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("retry-after") if hasattr(headers, "get") else None
    try:
        return float(value) if value else None
    except ValueError:
        return None


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        *,
        requests_per_minute: int = 10,
        max_attempts: int = 4,
        limiter: AsyncLimiter | None = None,
        timeout_seconds: float = 60,
        deadline_seconds: float = MAX_DEADLINE_SECONDS,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._deadline_seconds = deadline_seconds
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000))
        )
        self._limiter = limiter or AsyncLimiter(requests_per_minute, 60)
        self._max_attempts = max_attempts

    @classmethod
    def from_settings(cls, settings: Settings) -> "GeminiProvider":
        if settings.google_api_key is None:
            raise ProviderError("GOOGLE_API_KEY is not configured")
        return cls(
            settings.google_api_key.get_secret_value(),
            timeout_seconds=settings.provider_timeout_seconds,
            deadline_seconds=min(
                MAX_DEADLINE_SECONDS, DEADLINE_LEASE_FRACTION * settings.worker_lease_seconds
            ),
        )

    @trace_llm_call("google_genai")
    async def extract(
        self, document: ParsedDoc, schema: type[BaseModel], prompt: Prompt, cfg: RunConfig
    ) -> ProviderResult:
        tag_model(cfg.model)
        contents = self._build_contents(document, prompt)
        config = types.GenerateContentConfig(
            system_instruction=prompt.system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        with _tracer.start_as_current_span("gen_ai.generate_content") as span:
            span.set_attribute("gen_ai.system", self.name)
            span.set_attribute("gen_ai.request.model", cfg.model)
            started = time.perf_counter()
            response = await self._generate_with_retry(cfg.model, contents, config)
            latency_ms = int((time.perf_counter() - started) * 1000)
            usage = _usage_from(response)
            cost = cost_for(usage, self.name, cfg.model)
            finish_reason = _finish_reason(response)
            span.set_attribute("gen_ai.usage.input_tokens", usage.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", usage.output_tokens)
            span.set_attribute("gen_ai.response.finish_reason", finish_reason or "")
            span.set_attribute("cost.usd", float(cost.billed_usd))
            attrs = {"provider": self.name, "model": cfg.model}
            instruments = get_instruments()
            instruments.llm_tokens_total.add(usage.input_tokens, {**attrs, "direction": "input"})
            instruments.llm_tokens_total.add(usage.output_tokens, {**attrs, "direction": "output"})
            instruments.llm_cost_usd_total.add(float(cost.billed_usd), attrs)
            instruments.llm_latency_seconds.record(latency_ms / 1000, attrs)
        text = response.text or ""
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InvalidOutputError("gemini returned non-JSON output") from exc
        if not isinstance(parsed, dict):
            raise InvalidOutputError("gemini returned a JSON value that is not an object")
        return ProviderResult(
            model_output=parsed,
            usage=usage,
            latency_ms=latency_ms,
            raw_text=text,
            finish_reason=finish_reason,
        )

    def _build_contents(self, document: ParsedDoc, prompt: Prompt) -> list[Any]:
        if document.parser == "native" or document.scanned or document.text is None:
            part = types.Part.from_bytes(data=document.data, mime_type=document.mime_type)
            return [part, prompt.render_user(document_text="(attached file)")]
        return [prompt.render_user(document_text=document.text)]

    async def _generate_with_retry(
        self, model: str, contents: list[Any], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        """Retry outages within one total deadline (limiter waits, calls and back-off included)."""
        try:
            async with asyncio.timeout(self._deadline_seconds):
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(self._max_attempts)
                    | stop_after_delay(self._deadline_seconds),
                    wait=_wait,
                    retry=retry_if_exception_type((RateLimitedError, ProviderUnavailableError)),
                    reraise=True,
                ):
                    with attempt:
                        async with self._limiter:
                            try:
                                # The client's own timeout fires first; this cap covers any
                                # transport that ignores it.
                                async with asyncio.timeout(self._timeout_seconds + 5):
                                    return await self._client.aio.models.generate_content(
                                        model=model, contents=contents, config=config
                                    )
                            except Exception as exc:
                                raise map_error(exc) from exc
        except TimeoutError as exc:
            raise ProviderUnavailableError("gemini deadline exceeded") from exc
        raise ProviderUnavailableError("gemini retries exhausted")  # pragma: no cover


_base_wait = wait_exponential_jitter(initial=1, max=30)


def _wait(state: RetryCallState) -> float:
    exc = state.outcome.exception() if state.outcome else None
    if isinstance(exc, RateLimitedError) and exc.retry_after_seconds:
        return min(exc.retry_after_seconds, MAX_RETRY_AFTER_SECONDS)
    return _base_wait(state)


def _usage_from(response: types.GenerateContentResponse) -> Usage:
    meta = response.usage_metadata
    return Usage(
        input_tokens=(meta.prompt_token_count or 0) if meta else 0,
        output_tokens=(meta.candidates_token_count or 0) if meta else 0,
        cached_input_tokens=(meta.cached_content_token_count or 0) if meta else 0,
    )


def _finish_reason(response: types.GenerateContentResponse) -> str | None:
    candidates = response.candidates or []
    if not candidates or candidates[0].finish_reason is None:
        return None
    return str(candidates[0].finish_reason.name)


register("gemini", GeminiProvider.from_settings)
