"""SC-006: document content never reaches logs, spans or error messages."""

import io
import json
import logging
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any, cast

import structlog
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from contacompa.application.pipeline import extract
from contacompa.application.services.api_keys import mint_api_key
from contacompa.config import Settings
from contacompa.domain.config import RunConfig
from contacompa.domain.prompt import Prompt
from contacompa.entrypoints.api import middleware
from contacompa.entrypoints.api.deps import SignedInDep, require_admin_key
from contacompa.infrastructure.db.models import Company
from contacompa.infrastructure.observability.cost import Usage
from contacompa.infrastructure.observability.logging import configure_logging, get_logger
from contacompa.infrastructure.parsing.base import ParsedDoc
from contacompa.infrastructure.providers.base import InvalidOutputError, ProviderResult
from tests.conftest import make_invoice_pdf

SENTINEL = "SENTINEL-ULTRA-SECRET-VENDOR-9931"
ADMIN_KEY = "SENTINEL-ADMIN-KEY-5521"
PRESENTED_API_KEY = "SENTINEL-PRESENTED-API-KEY-7743"


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


@contextmanager
def captured_logs() -> Iterator[io.StringIO]:
    configure_logging()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    handler.setFormatter(root.handlers[0].formatter)
    root.addHandler(handler)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        structlog.contextvars.clear_contextvars()


async def test_document_content_never_logged() -> None:
    with captured_logs() as stream:
        pdf = make_invoice_pdf(vendor=SENTINEL)
        cfg = RunConfig(provider="gemini", model="gemini-2.5-flash", parser="pymupdf")
        error: str = ""
        try:
            await extract(pdf, cfg, LeakyProvider())
        except InvalidOutputError as exc:
            error = str(exc)
        get_logger("test").info("after", stage="done")
    logged = stream.getvalue()
    assert SENTINEL not in logged
    assert SENTINEL not in error
    lines: list[dict[str, Any]] = [json.loads(line) for line in logged.splitlines() if line.strip()]
    assert any(entry.get("event") == "after" for entry in lines)


class _Result:
    """The seeded company for a RUC lookup; no live key for a key lookup."""

    def scalar_one_or_none(self) -> Company:
        return Company(ruc="20543306771", legal_name="CIMMSA")

    def one_or_none(self) -> None:
        return None


class _FakeSession:
    """Stands in for the database: one seeded company, no stored keys."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        return _Result()

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass


async def test_api_keys_and_admin_key_never_logged() -> None:
    """FR-018: presented keys, the admin key and a freshly minted key stay out of logs."""
    session = _FakeSession()

    @asynccontextmanager
    async def sessions() -> Any:
        yield session

    app = FastAPI()
    middleware.install(app)
    app.state.settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://unused/unused",
        blob_dir="/tmp/blobs",  # noqa: S108
        admin_api_key=ADMIN_KEY,
    )
    app.state.sessions = sessions

    @app.post("/mint", dependencies=[Depends(require_admin_key)])
    async def mint() -> dict[str, str]:
        minted = await mint_api_key(cast(AsyncSession, session), "20543306771")
        return {"api_key": minted.api_key}

    @app.get("/me")
    async def me(signed_in: SignedInDep) -> dict[str, str]:
        return {"ruc": signed_in.company.ruc}

    with captured_logs() as stream:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            wrong = await client.post("/mint", headers={"X-Admin-Key": PRESENTED_API_KEY})
            minted = await client.post("/mint", headers={"X-Admin-Key": ADMIN_KEY})
            rejected = await client.get("/me", headers={"X-API-Key": PRESENTED_API_KEY})
    assert wrong.status_code == 401
    assert minted.status_code == 200
    assert rejected.status_code == 401
    minted_key = minted.json()["api_key"]
    logged = stream.getvalue()
    assert minted_key
    for secret in (ADMIN_KEY, PRESENTED_API_KEY, minted_key):
        assert secret not in logged
        assert secret not in wrong.text + rejected.text
