from typing import Any, Protocol

from pydantic import BaseModel

from contacompa.domain.config import RunConfig
from contacompa.domain.prompt import Prompt
from contacompa.infrastructure.observability.cost import Usage
from contacompa.infrastructure.parsing.base import ParsedDoc


class ProviderError(Exception):
    """Base for every provider failure. The message never contains document content."""


class RateLimitedError(ProviderError):
    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ProviderUnavailableError(ProviderError):
    """5xx, connection failure or timeout — worth retrying."""


class InvalidOutputError(ProviderError):
    """The model returned something that is not a JSON object matching the request."""


def is_outage(exc: BaseException) -> bool:
    """A failure that says the provider is down, not that this request is bad."""
    return isinstance(exc, ProviderUnavailableError | RateLimitedError)


class ProviderResult(BaseModel):
    model_output: dict[str, Any]
    usage: Usage
    latency_ms: int
    raw_text: str | None = None
    finish_reason: str | None = None


class Provider(Protocol):
    name: str

    async def extract(
        self, document: ParsedDoc, schema: type[BaseModel], prompt: Prompt, cfg: RunConfig
    ) -> ProviderResult: ...
