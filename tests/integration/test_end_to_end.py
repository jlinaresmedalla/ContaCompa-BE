"""T018: submit a PDF, let the worker process it, fetch the result.

Gemini's answer is replayed from a recorded cassette, so the test needs no network or key.
Re-record after a prompt, schema or model change (needs GOOGLE_API_KEY in the environment).
The purchase_doc schema (ADR 0005) needs a fresh recording:
    uv run pytest -m integration tests/integration/test_end_to_end.py --record-mode=rewrite
"""

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contacompa.config import Settings
from contacompa.domain.schemas import get_schema
from contacompa.entrypoints.api.app import create_app
from contacompa.entrypoints.worker import Worker
from contacompa.infrastructure.blob import LocalBlobStore
from contacompa.infrastructure.db import queue
from tests.conftest import make_invoice_pdf

pytestmark = pytest.mark.integration
HEADERS = {"X-API-Key": "test-key"}


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, Any]:
    return {
        "filter_headers": ["x-goog-api-key", "authorization"],
        "filter_query_parameters": ["key"],
        "decode_compressed_response": True,
        "ignore_localhost": True,
        "ignore_hosts": ["t"],
    }


@pytest.fixture(scope="module")
def vcr_cassette_dir() -> str:
    return str(Path(__file__).parents[1] / "fixtures" / "cassettes")


@pytest.fixture
def settings(pg_url: str, tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=pg_url,
        api_key="test-key",
        blob_dir=str(tmp_path / "blobs"),
        google_api_key=os.environ.get("GOOGLE_API_KEY", "replay-key"),
    )


@pytest.fixture
async def client(
    settings: Settings, sessions: async_sessionmaker[AsyncSession]
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings, telemetry=False)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


@pytest.mark.vcr
async def test_submit_process_and_fetch_result(
    client: AsyncClient, settings: Settings, sessions: async_sessionmaker[AsyncSession]
) -> None:
    submitted = await client.post(
        "/v1/documents",
        headers=HEADERS,
        files={"file": ("inv.pdf", make_invoice_pdf(), "application/pdf")},
        data={"config": json.dumps({"parser": "pymupdf"})},
    )
    assert submitted.status_code == 202, submitted.text
    ids = submitted.json()

    worker = Worker(settings, sessions, LocalBlobStore(Path(settings.blob_dir)))
    async with sessions() as session:
        job = await queue.claim(session, "w-test")
        await session.commit()
    assert job is not None
    await worker.process(job, "w-test")

    status = (await client.get(f"/v1/jobs/{ids['job_id']}", headers=HEADERS)).json()
    assert status["status"] == "done", status
    result = await client.get(f"/v1/documents/{ids['document_id']}/result", headers=HEADERS)
    assert result.status_code == 200, result.text
    body = result.json()

    expected_fields = set(get_schema("purchase_doc").model.model_fields) - {"doc_type"}
    assert expected_fields <= set(body["fields"])
    assert body["schema_type"] == "purchase_doc" and body["purchase_doc_id"] is not None
    assert body["fields"]["total_amount"]["value"] == "1234.56"
    assert body["fields"]["total_amount"]["verified"] is True
    usage = body["usage"]
    assert usage["provider"] == "gemini" and usage["model"] == settings.default_model
    assert usage["parser"] == "pymupdf" and usage["input_tokens"] > 0
    assert usage["cost_usd"] == "0.000000"  # free tier: billed cost is zero
    assert usage["turnaround_ms"] > 0
