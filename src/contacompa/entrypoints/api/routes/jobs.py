from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from contacompa.entrypoints.api.deps import CompanyDep, SessionDep
from contacompa.infrastructure.db.models import Job, JobStatus
from contacompa.infrastructure.db.repos import get_document, get_job

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


def job_payload(job: Job) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "job_id": str(job.id),
        "document_id": str(job.document_id),
        "status": job.status.value,
        "attempts": job.attempts,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
    if job.last_error:
        payload["last_error"] = job.last_error
    if job.status is JobStatus.DONE:
        payload["result_url"] = f"/v1/documents/{job.document_id}/result"
    return payload


@router.get("/{job_id}")
async def get_job_status(job_id: UUID, session: SessionDep, company: CompanyDep) -> dict[str, Any]:
    job = await get_job(session, job_id)
    document = await get_document(session, job.document_id) if job else None
    if job is None or document is None or document.company_id != company.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return job_payload(job)
