"""PostgreSQL-backed job queue. One statement per transition; claiming uses
`FOR UPDATE SKIP LOCKED` so any number of workers can poll the same table safely."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from contacompa.infrastructure.db.models import Job, JobStatus

BACKOFF_BASE = timedelta(seconds=5)
BACKOFF_CAP = timedelta(minutes=10)
MAX_ERROR_LEN = 1000


def backoff_for(attempts: int) -> timedelta:
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    delay: timedelta = BACKOFF_BASE * (2 ** (attempts - 1))
    return min(delay, BACKOFF_CAP)


def _now() -> datetime:
    return datetime.now(UTC)


async def enqueue(
    session: AsyncSession, document_id: UUID, config: dict[str, object], *, max_attempts: int = 5
) -> Job:
    job = Job(document_id=document_id, config=config, max_attempts=max_attempts, run_after=_now())
    session.add(job)
    await session.flush()
    return job


async def claim(session: AsyncSession, worker_id: str) -> Job | None:
    now = _now()
    candidate = (
        select(Job.id)
        .where(Job.status == JobStatus.QUEUED, Job.run_after <= now)
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    stmt = (
        update(Job)
        .where(Job.id == candidate)
        .values(
            status=JobStatus.PROCESSING,
            locked_at=now,
            locked_by=worker_id,
            started_at=func.coalesce(Job.started_at, now),
        )
        .returning(Job)
    )
    job = (await session.execute(stmt)).scalar_one_or_none()
    return job


async def complete(session: AsyncSession, job_id: UUID, worker_id: str) -> bool:
    """Mark the job done inside the caller's transaction; does not commit.

    Returns False when the job is no longer processing under `worker_id` (its lease expired and
    another worker reclaimed it). The UPDATE also locks the job row, so the caller's other writes
    in the same transaction are serialized against any competing worker."""
    stmt = (
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.PROCESSING, Job.locked_by == worker_id)
        .values(status=JobStatus.DONE, finished_at=_now(), locked_at=None, locked_by=None)
        .returning(Job.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def fail(session: AsyncSession, job_id: UUID, error: str, worker_id: str) -> JobStatus | None:
    """Record a failed attempt and requeue with backoff, or move to DEAD at max_attempts.

    Returns None (and changes nothing) when `worker_id` no longer holds the job's lease."""
    job = (
        await session.execute(
            select(Job)
            .where(Job.id == job_id, Job.status == JobStatus.PROCESSING, Job.locked_by == worker_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        return None
    job.attempts += 1
    job.last_error = error[:MAX_ERROR_LEN]
    job.locked_at = None
    job.locked_by = None
    if job.attempts >= job.max_attempts:
        job.status = JobStatus.DEAD
        job.finished_at = _now()
    else:
        job.status = JobStatus.QUEUED
        job.run_after = _now() + backoff_for(job.attempts)
    status = job.status
    return status


async def release(
    session: AsyncSession, job_id: UUID, run_after: datetime, reason: str, worker_id: str
) -> bool:
    """Send a job back to the queue without spending an attempt (a provider outage, not a defect).

    One statement, guarded by the lease like `fail`. Returns False when `worker_id` no longer
    holds the job's lease."""
    stmt = (
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.PROCESSING, Job.locked_by == worker_id)
        .values(
            status=JobStatus.QUEUED,
            locked_at=None,
            locked_by=None,
            run_after=run_after,
            last_error=reason[:MAX_ERROR_LEN],
        )
        .returning(Job.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def reap_expired(session: AsyncSession, lease: timedelta) -> int:
    cutoff = _now() - lease
    stmt = (
        update(Job)
        .where(Job.status == JobStatus.PROCESSING, Job.locked_at < cutoff)
        .values(status=JobStatus.QUEUED, locked_at=None, locked_by=None, run_after=_now())
        .returning(Job.id)
    )
    reaped = (await session.execute(stmt)).scalars().all()
    return len(reaped)


async def stats(session: AsyncSession) -> tuple[int, float]:
    row = (
        await session.execute(
            select(func.count(Job.id), func.min(Job.created_at)).where(
                Job.status == JobStatus.QUEUED
            )
        )
    ).one()
    count, oldest = int(row[0]), row[1]
    age = (_now() - oldest).total_seconds() if oldest is not None else 0.0
    return count, max(age, 0.0)
