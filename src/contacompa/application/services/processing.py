"""Record a finished extraction: one transaction for job status, raw result, reviewed purchase
doc, and spend. A crash before the commit leaves nothing behind, so the retry starts clean."""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contacompa.application.pipeline.extract import ExtractionOutcome
from contacompa.application.services.purchase_docs import materialize
from contacompa.domain.config import RunConfig
from contacompa.infrastructure.db import queue
from contacompa.infrastructure.db.models import Company, Document, ExtractionResult, Job
from contacompa.infrastructure.db.repos import add_daily_spend


async def record_success(
    sessions: async_sessionmaker[AsyncSession],
    *,
    job: Job,
    worker_id: str,
    cfg: RunConfig,
    outcome: ExtractionOutcome,
    turnaround_ms: int,
) -> bool:
    """False when another worker owns the job's lease; then nothing is written."""
    async with sessions() as session:
        if not await queue.complete(session, job.id, worker_id):
            await session.rollback()
            return False
        result = ExtractionResult(
            job_id=job.id,
            document_id=job.document_id,
            schema_type=outcome.schema_type,
            schema_version=outcome.schema_version,
            fields=outcome.fields,
            missing=[m.model_dump() for m in outcome.missing],
            provider=cfg.provider,
            model=cfg.model,
            prompt_version=outcome.prompt_version,
            parser=outcome.parser,
            effort=cfg.effort,
            input_tokens=outcome.usage.input_tokens,
            output_tokens=outcome.usage.output_tokens,
            cost_usd=outcome.cost.billed_usd,
            latency_ms=outcome.latency_ms,
            turnaround_ms=turnaround_ms,
        )
        session.add(result)
        await session.flush()
        document = await session.get(Document, job.document_id)
        if document is None:
            raise ValueError("document row missing")
        company = await session.get(Company, document.company_id)
        if company is None:
            raise ValueError("company row missing")
        if document.page_count is None and outcome.page_count is not None:
            document.page_count = outcome.page_count
        await materialize(
            session,
            company=company,
            document=document,
            result_id=result.id,
            doc_type=outcome.doc_type,
            fields=outcome.fields,
        )
        await add_daily_spend(session, Decimal(outcome.cost.billed_usd))
        await session.commit()
    return True
