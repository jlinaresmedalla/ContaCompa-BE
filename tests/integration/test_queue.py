import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contacompa.infrastructure.db import queue
from contacompa.infrastructure.db.models import JobStatus
from tests.integration.conftest import make_company, make_document_row

pytestmark = pytest.mark.integration


async def make_document(session: AsyncSession) -> uuid.UUID:
    company = await make_company(session)
    doc = make_document_row(company, uuid.uuid4().hex * 2, "k", 10)
    session.add(doc)
    await session.commit()
    return doc.id


async def test_concurrent_claim_is_exclusive(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as s:
        job = await queue.enqueue(s, await make_document(s), {"provider": "x"})
        await s.commit()
    async with sessions() as a, sessions() as b:
        first, second = await asyncio.gather(queue.claim(a, "w1"), queue.claim(b, "w2"))
        await a.commit()
        await b.commit()
    claimed = [j for j in (first, second) if j is not None]
    assert len(claimed) == 1
    assert claimed[0].id == job.id
    assert claimed[0].status is JobStatus.PROCESSING and claimed[0].locked_by in {"w1", "w2"}


async def test_claim_order_and_stats(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as s:
        doc = await make_document(s)
        ids = [(await queue.enqueue(s, doc, {"n": i})).id for i in range(3)]
        await s.commit()
        count, age = await queue.stats(s)
        assert count == 3 and age >= 0
        claimed = [await queue.claim(s, "w") for _ in range(3)]
    assert [j.id for j in claimed if j] == ids


async def test_fail_backoff_and_dead(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as s:
        job = await queue.enqueue(s, await make_document(s), {}, max_attempts=3)
        await s.commit()
        assert (await queue.claim(s, "w")) is not None
        assert await queue.fail(s, job.id, "boom " * 500, "w") is JobStatus.QUEUED
        refreshed = await s.get(job.__class__, job.id)
        assert refreshed is not None and refreshed.attempts == 1
        assert refreshed.last_error is not None and len(refreshed.last_error) == queue.MAX_ERROR_LEN
        assert refreshed.run_after > datetime.now(UTC)
        assert await queue.claim(s, "w") is None
        await s.execute(text("UPDATE jobs SET run_after = now() - interval '1 minute'"))
        await s.commit()
        assert (await queue.claim(s, "w")) is not None
        assert await queue.fail(s, job.id, "again", "w") is JobStatus.QUEUED
        await s.execute(text("UPDATE jobs SET run_after = now() - interval '1 minute'"))
        await s.commit()
        assert (await queue.claim(s, "w")) is not None
        assert await queue.fail(s, job.id, "final", "w") is JobStatus.DEAD
        dead = await s.get(job.__class__, job.id)
        assert dead is not None and dead.finished_at is not None
        await s.execute(text("UPDATE jobs SET run_after = now() - interval '1 minute'"))
        await s.commit()
        assert await queue.claim(s, "w") is None


async def test_reap_expired_and_complete(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as s:
        doc = await make_document(s)
        stale = await queue.enqueue(s, doc, {})
        fresh = await queue.enqueue(s, doc, {})
        await s.commit()
        assert await queue.claim(s, "w") is not None
        assert await queue.claim(s, "w") is not None
        await s.execute(
            text("UPDATE jobs SET locked_at = now() - interval '20 minutes' WHERE id = :id"),
            {"id": stale.id},
        )
        await s.commit()
        assert await queue.reap_expired(s, timedelta(minutes=5)) == 1
        reaped = await s.get(stale.__class__, stale.id)
        assert reaped is not None
        await s.refresh(reaped)
        assert reaped.status is JobStatus.QUEUED and reaped.locked_by is None
        assert await queue.complete(s, stale.id, "w") is False
        assert await queue.complete(s, fresh.id, "w") is True
        await s.commit()
        done = await s.get(fresh.__class__, fresh.id)
        assert done is not None
        await s.refresh(done)
        assert done.status is JobStatus.DONE and done.locked_at is None


async def test_lost_lease_blocks_complete_and_fail(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as s:
        job = await queue.enqueue(s, await make_document(s), {})
        await s.commit()
        job_id, job_cls = job.id, job.__class__
        assert (await queue.claim(s, "w1")) is not None
        await s.execute(text("UPDATE jobs SET locked_by = 'w2' WHERE id = :id"), {"id": job_id})
        await s.commit()
        assert await queue.complete(s, job_id, "w1") is False
        await s.rollback()
        assert await queue.fail(s, job_id, "late failure", "w1") is None
        current = await s.get(job_cls, job_id)
        assert current is not None
        await s.refresh(current)
        assert current.status is JobStatus.PROCESSING
        assert current.locked_by == "w2" and current.attempts == 0
