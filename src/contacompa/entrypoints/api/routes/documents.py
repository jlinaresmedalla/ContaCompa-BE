import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from contacompa.application.services.intake import IntakeError, submit
from contacompa.domain.config import RunConfig
from contacompa.entrypoints.api.deps import BlobsDep, CompanyDep, SessionDep, SettingsDep
from contacompa.entrypoints.api.routes.jobs import job_payload
from contacompa.infrastructure.db.repos import (
    get_document,
    get_result_for_document,
    latest_job_for_document,
)

router = APIRouter(prefix="/v1/documents", tags=["documents"])

_INTAKE_STATUS = {
    "unsupported": status.HTTP_400_BAD_REQUEST,
    "too_many_pages": status.HTTP_400_BAD_REQUEST,
    "too_large": status.HTTP_413_CONTENT_TOO_LARGE,
    "unreadable": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "paused": status.HTTP_429_TOO_MANY_REQUESTS,
}


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def submit_document(
    session: SessionDep,
    blobs: BlobsDep,
    settings: SettingsDep,
    company: CompanyDep,
    file: Annotated[
        UploadFile, File(description="PDF, JPEG or PNG; configured server limit applies")
    ],
    config: Annotated[str | None, Form()] = None,
) -> JSONResponse:
    run_config = _run_config(config, settings)
    data = await file.read(settings.max_upload_bytes + 1)
    try:
        submitted = await submit(
            session,
            blobs,
            settings,
            company,
            data=data,
            filename=file.filename,
            run_config=run_config,
        )
    except IntakeError as exc:
        headers = {"Retry-After": "3600"} if exc.kind == "paused" else None
        raise HTTPException(_INTAKE_STATUS[exc.kind], str(exc), headers=headers) from exc
    return JSONResponse(
        status_code=status.HTTP_200_OK if submitted.duplicate else status.HTTP_202_ACCEPTED,
        content={
            "document_id": str(submitted.document_id),
            "job_id": str(submitted.job_id) if submitted.job_id else None,
            "duplicate": submitted.duplicate,
        },
    )


def _run_config(raw: str | None, settings: SettingsDep) -> RunConfig:
    defaults = {
        "provider": settings.default_provider,
        "model": settings.default_model,
        "parser": settings.default_parser,
        "prompt_version": "v1",
    }
    if raw is None or raw == "":
        return RunConfig.model_validate(defaults)
    try:
        overrides = json.loads(raw)
        if not isinstance(overrides, dict):
            raise TypeError
        return RunConfig.model_validate({**defaults, **overrides})
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid config override") from exc


@router.get("/{document_id}/file", response_class=Response)
async def get_file(
    document_id: UUID, session: SessionDep, blobs: BlobsDep, company: CompanyDep
) -> Response:
    """The original upload (PDF or photo), for side-by-side review."""
    document = await get_document(session, document_id)
    if document is None or document.company_id != company.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    data = await blobs.get(document.storage_key)
    return Response(
        content=data,
        media_type=document.mime_type,
        headers={"Content-Disposition": f'inline; filename="{document.filename}"'},
    )


@router.get("/{document_id}/result")
async def get_result(document_id: UUID, session: SessionDep, company: CompanyDep) -> dict[str, Any]:
    """The raw extraction: what the model returned, before review."""
    document = await get_document(session, document_id)
    if document is None or document.company_id != company.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    result = await get_result_for_document(session, document_id)
    if result is None:
        job = await latest_job_for_document(session, document_id)
        detail: dict[str, Any] = {"message": "no result yet"}
        if job is not None:
            detail["job"] = job_payload(job)
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail)
    return {
        "document_id": str(document_id),
        "job_id": str(result.job_id),
        "purchase_doc_id": str(document.purchase_doc_id) if document.purchase_doc_id else None,
        "schema_type": result.schema_type,
        "schema_version": result.schema_version,
        "fields": result.fields,
        "missing": result.missing,
        "usage": {
            "provider": result.provider,
            "model": result.model,
            "prompt_version": result.prompt_version,
            "parser": result.parser,
            "effort": result.effort,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": str(result.cost_usd),
            "latency_ms": result.latency_ms,
            "turnaround_ms": result.turnaround_ms,
        },
    }
