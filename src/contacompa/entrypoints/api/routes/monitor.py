from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from contacompa.application.services import monitor
from contacompa.application.services.costs import cost_report
from contacompa.application.services.monitor import RetryError
from contacompa.entrypoints.api.deps import CompanyDep, SessionDep
from contacompa.entrypoints.api.schemas import CostReportOut, JobRowOut, MonitorOut, ProviderOut

router = APIRouter(prefix="/v1", tags=["monitor and costs"])


@router.get("/monitor")
async def get_monitor(
    session: SessionDep,
    company: CompanyDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> MonitorOut:
    """Job counts by status, the latest jobs, and the model provider's breaker state."""
    counts = await monitor.job_counts(session, company.id)
    rows = await monitor.recent_jobs(session, company.id, limit)
    provider = await monitor.provider_info(session)
    return MonitorOut(
        counts=counts,
        jobs=[JobRowOut.build(row) for row in rows],
        provider=ProviderOut.build(provider) if provider else None,
    )


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_job(job_id: UUID, session: SessionDep, company: CompanyDep) -> dict[str, str]:
    try:
        new_job_id = await monitor.retry_job(session, company.id, job_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except RetryError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"job_id": str(new_job_id)}


@router.get("/costs")
async def get_costs(
    session: SessionDep,
    company: CompanyDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> CostReportOut:
    """Billed cost (zero on a free tier) and list-price cost of the same tokens."""
    return CostReportOut.build(await cost_report(session, company.id, days))
