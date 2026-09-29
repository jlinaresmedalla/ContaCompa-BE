import asyncio
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types
from tenacity import RetryCallState

from contacompa.infrastructure.providers.base import (
    ProviderError,
    ProviderUnavailableError,
    RateLimitedError,
)
from contacompa.infrastructure.providers.gemini import GeminiProvider, _wait, map_error


def _api_error(code: int, retry_after: str | None = None) -> genai_errors.APIError:
    headers = {"retry-after": retry_after} if retry_after else {}
    response = SimpleNamespace(headers=headers, status_code=code)
    return genai_errors.APIError(code, {"message": "boom", "status": "ERR"}, response)


def test_map_error_rate_limit_with_retry_after() -> None:
    mapped = map_error(_api_error(429, "7"))
    assert isinstance(mapped, RateLimitedError)
    assert mapped.retry_after_seconds == 7.0


def test_map_error_server_and_client() -> None:
    assert isinstance(map_error(_api_error(503)), ProviderUnavailableError)
    assert isinstance(map_error(_api_error(408)), ProviderUnavailableError)
    client_side = map_error(_api_error(400))
    assert type(client_side) is ProviderError


def test_map_error_transport() -> None:
    assert isinstance(map_error(TimeoutError()), ProviderUnavailableError)
    assert isinstance(map_error(ConnectionError()), ProviderUnavailableError)
    assert isinstance(map_error(httpx.ReadTimeout("slow")), ProviderUnavailableError)
    assert isinstance(map_error(httpx.ConnectError("refused")), ProviderUnavailableError)
    assert type(map_error(RuntimeError("x"))) is ProviderError


@pytest.mark.parametrize("value", ["", "abc"])
def test_map_error_bad_retry_after(value: str) -> None:
    mapped = map_error(_api_error(429, value))
    assert isinstance(mapped, RateLimitedError)
    assert mapped.retry_after_seconds is None


def test_retry_after_is_capped() -> None:
    state = SimpleNamespace(outcome=SimpleNamespace(exception=lambda: RateLimitedError("x", 600)))
    assert _wait(cast(RetryCallState, state)) == 60


async def test_hung_provider_ends_at_the_deadline_as_unavailable() -> None:
    async def hang(**_: object) -> None:
        await asyncio.sleep(3600)

    provider = GeminiProvider("k", deadline_seconds=0.05)
    provider._client = cast(
        Any, SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=hang)))
    )

    with pytest.raises(ProviderUnavailableError, match="deadline"):
        await provider._generate_with_retry("m", [], types.GenerateContentConfig())
