"""Worker.process writes result, purchase doc, spend and job status in one transaction, and
writes nothing
when another worker has taken over the job's lease."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contacompa.application.pipeline.extract import ExtractionOutcome
from contacompa.config import get_settings
from contacompa.entrypoints import worker as worker_module
from contacompa.infrastructure.blob import LocalBlobStore, sha256_hex
from contacompa.infrastructure.db import queue
from contacompa.infrastructure.db.models import (
    DailySpend,
    ExtractionResult,
    Job,
    JobStatus,
    PurchaseDoc,
)
from contacompa.infrastructure.observability.cost import Cost, Usage
from tests.integration.conftest import make_company, make_document_row

pytestmark = pytest.mark.integration

COST = Decimal("0.001250")


async def fake_extract(*_: Any, **__: Any) -> ExtractionOutcome:
    return ExtractionOutcome(
        schema_type="purchase_doc",
        schema_version="1",
        doc_type="invoice",
        fields={"total_amount": {"value": "10.00"}},
        missing=[],
        usage=Usage(input_tokens=1000, output_tokens=200),
        cost=Cost(billed_usd=COST, list_usd=COST, free_tier=False),
        latency_ms=120,
        page_count=1,
        scanned=False,
        parser="native",
        prompt_version="v1",
    )


async def setup_job(
    sessions: async_sessionmaker[AsyncSession], blobs: LocalBlobStore, pdf: bytes
) -> Job:
    key = sha256_hex(pdf)
    await blobs.put(key, pdf)
    async with sessions() as s:
        company = await make_company(s)
        doc = make_document_row(company, key, key, len(pdf))
        s.add(doc)
        await s.commit()
        await queue.enqueue(s, doc.id, {"provider": "fake", "model": "m"})
        job = await queue.claim(s, "w1")
        await s.commit()
    assert job is not None
    return job


def make_worker(
    sessions: async_sessionmaker[AsyncSession],
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> worker_module.Worker:
    monkeypatch.setattr(worker_module, "extract", fake_extract)
    worker = worker_module.Worker(get_settings(), sessions, blobs)
    monkeypatch.setattr(worker, "provider", lambda _name: object())
    return worker


async def counts(sessions: async_sessionmaker[AsyncSession]) -> tuple[int, int, Decimal]:
    async with sessions() as s:
        results = (await s.execute(select(func.count(ExtractionResult.id)))).scalar_one()
        purchase_docs = (await s.execute(select(func.count(PurchaseDoc.id)))).scalar_one()
        spend = (
            await s.execute(select(func.coalesce(func.sum(DailySpend.cost_usd), 0)))
        ).scalar_one()
    return int(results), int(purchase_docs), Decimal(spend)


async def test_process_commits_result_spend_and_status_together(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    invoice_pdf: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blobs = LocalBlobStore(tmp_path)
    job = await setup_job(sessions, blobs, invoice_pdf)
    await make_worker(sessions, blobs, monkeypatch).process(job, "w1")

    assert await counts(sessions) == (1, 1, COST)
    async with sessions() as s:
        done = await s.get(Job, job.id)
        assert done is not None and done.status is JobStatus.DONE and done.locked_by is None


async def test_process_writes_nothing_after_lease_lost(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    invoice_pdf: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blobs = LocalBlobStore(tmp_path)
    job = await setup_job(sessions, blobs, invoice_pdf)
    async with sessions() as s:
        await s.execute(text("UPDATE jobs SET locked_by = 'w2' WHERE id = :id"), {"id": job.id})
        await s.commit()

    await make_worker(sessions, blobs, monkeypatch).process(job, "w1")

    assert await counts(sessions) == (0, 0, Decimal(0))
    async with sessions() as s:
        current = await s.get(Job, job.id)
        assert current is not None
        assert current.status is JobStatus.PROCESSING and current.locked_by == "w2"
        assert current.attempts == 0
