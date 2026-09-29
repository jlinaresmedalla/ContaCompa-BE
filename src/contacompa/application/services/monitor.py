"""What the worker is doing: job counts by status and the latest uploads with their state."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contacompa.domain.checks import has_warnings
from contacompa.domain.config import RunConfig
from contacompa.infrastructure.db import queue
from contacompa.infrastructure.db.models import Document, Job, JobStatus, PurchaseDoc
from contacompa.infrastructure.db.repos import get_provider_status


class RetryError(Exception):
    pass


@dataclass(frozen=True)
class JobRow:
    job_id: UUID
    document_id: UUID
    filename: str
    source_kind: str
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime
    finished_at: datetime | None
    purchase_doc_id: UUID | None
    observations: int
    warnings: bool
    doc_number: str | None


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    state: str
    open_until: datetime | None
    reason: str | None


async def provider_info(session: AsyncSession) -> ProviderInfo | None:
    """The model provider's breaker state as the worker last wrote it; None before any change."""
    row = await get_provider_status(session)
    if row is None:
        return None
    return ProviderInfo(row.provider, row.state, row.open_until, row.reason)


async def job_counts(session: AsyncSession, company_id: UUID) -> dict[str, int]:
    stmt = (
        select(Job.status, func.count())
        .join(Document, Document.id == Job.document_id)
        .where(Document.company_id == company_id)
        .group_by(Job.status)
    )
    counts = {status.value: 0 for status in JobStatus}
    for status, count in (await session.execute(stmt)).all():
        counts[status.value] = int(count)
    return counts


async def recent_jobs(session: AsyncSession, company_id: UUID, limit: int = 50) -> list[JobRow]:
    stmt = (
        select(Job, Document, PurchaseDoc)
        .join(Document, Document.id == Job.document_id)
        .outerjoin(PurchaseDoc, PurchaseDoc.id == Document.purchase_doc_id)
        .where(Document.company_id == company_id)
        .order_by(Job.created_at.desc())
        .limit(limit)
    )
    rows = []
    for job, document, purchase_doc in (await session.execute(stmt)).all():
        rows.append(
            JobRow(
                job_id=job.id,
                document_id=document.id,
                filename=document.filename,
                source_kind=document.source_kind.value,
                status=job.status.value,
                attempts=job.attempts,
                last_error=job.last_error,
                created_at=job.created_at,
                finished_at=job.finished_at,
                purchase_doc_id=purchase_doc.id if purchase_doc else None,
                observations=len(purchase_doc.issues) if purchase_doc else 0,
                warnings=has_warnings(purchase_doc.issues) if purchase_doc else False,
                doc_number=purchase_doc.doc_number if purchase_doc else None,
            )
        )
    return rows


async def retry_job(session: AsyncSession, company_id: UUID, job_id: UUID) -> UUID:
    """Queue a fresh job for a document whose job died (retries exhausted)."""
    job = await session.get(Job, job_id)
    document = await session.get(Document, job.document_id) if job else None
    if job is None or document is None or document.company_id != company_id:
        raise LookupError("job not found")
    if job.status is not JobStatus.DEAD:
        raise RetryError(f"only dead jobs can be retried; this one is {job.status.value}")
    config = RunConfig.model_validate(job.config).model_dump()
    new_job = await queue.enqueue(session, document.id, config)
    await session.commit()
    return new_job.id
