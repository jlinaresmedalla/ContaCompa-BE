from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contacompa.config import Settings
from contacompa.entrypoints.api.app import create_app
from contacompa.infrastructure.db.models import Job
from contacompa.infrastructure.db.repos import add_daily_spend
from tests.conftest import make_invoice_pdf
from tests.integration.conftest import make_company

pytestmark = pytest.mark.integration
HEADERS = {"X-API-Key": "test-key"}


@pytest.fixture
async def client(
    pg_url: str, tmp_path: Path, sessions: async_sessionmaker[AsyncSession]
) -> AsyncIterator[AsyncClient]:
    settings = Settings(_env_file=None, database_url=pg_url, blob_dir=str(tmp_path / "blobs"))
    app = create_app(settings, telemetry=False)
    async with app.router.lifespan_context(app):
        async with sessions() as session:
            await make_company(session)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def submit(
    client: AsyncClient,
    pdf: bytes,
    *,
    headers: dict[str, str] | None = HEADERS,
    config: str | None = None,
) -> Response:
    data = {}
    if config is not None:
        data["config"] = config
    return await client.post(
        "/v1/documents",
        headers=headers or {},
        files={"file": ("inv.pdf", pdf, "application/pdf")},
        data=data,
    )


async def test_ops_endpoints(client: AsyncClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    ready = await client.get("/readyz")
    assert ready.status_code == 200 and ready.json()["db"] == "ok"
    assert "version" in (await client.get("/version")).json()


async def test_submit_status_and_duplicate(
    client: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    pdf = make_invoice_pdf()
    first = await submit(client, pdf)
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["duplicate"] is False and "x-request-id" in first.headers
    status = await client.get(f"/v1/jobs/{body['job_id']}", headers=HEADERS)
    assert status.json()["status"] == "queued"
    again = await submit(client, pdf)
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert again.json()["document_id"] == body["document_id"]
    async with sessions() as session:
        assert await session.scalar(select(func.count(Job.id))) == 1
    no_result = await client.get(f"/v1/documents/{body['document_id']}/result", headers=HEADERS)
    assert no_result.status_code == 404 and no_result.json()["error"]["code"] == "not_found"


async def test_rejections(client: AsyncClient) -> None:
    pdf = make_invoice_pdf()
    assert (await submit(client, pdf, headers=None)).status_code == 401
    assert (await submit(client, b"hello")).status_code == 400
    assert (await submit(client, pdf + b"0" * (10 * 1024 * 1024))).status_code == 413
    assert (await submit(client, make_invoice_pdf(pages=11))).status_code == 400
    assert (await submit(client, pdf, config="[1]")).status_code == 400
    missing = await client.get("/v1/jobs/00000000-0000-0000-0000-000000000000", headers=HEADERS)
    assert missing.status_code == 404


async def test_cost_ceiling_pauses_intake(
    client: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session:
        await add_daily_spend(session, Decimal("5"))
        await session.commit()
    resp = await submit(client, make_invoice_pdf(number="X"))
    assert resp.status_code == 429 and resp.headers["retry-after"] == "3600"
