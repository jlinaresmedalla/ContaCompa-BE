from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from contacompa.infrastructure.providers.base import (
    ProviderError,
    ProviderUnavailableError,
    RateLimitedError,
)
from contacompa.infrastructure.providers.gemini import map_error


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
    client_side = map_error(_api_error(400))
    assert type(client_side) is ProviderError


def test_map_error_transport() -> None:
    assert isinstance(map_error(TimeoutError()), ProviderUnavailableError)
    assert isinstance(map_error(ConnectionError()), ProviderUnavailableError)
    assert type(map_error(RuntimeError("x"))) is ProviderError


@pytest.mark.parametrize("value", ["", "abc"])
def test_map_error_bad_retry_after(value: str) -> None:
    mapped = map_error(_api_error(429, value))
    assert isinstance(mapped, RateLimitedError)
    assert mapped.retry_after_seconds is None
