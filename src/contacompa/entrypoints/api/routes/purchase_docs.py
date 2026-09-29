from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from contacompa.application.services import records
from contacompa.application.services.observations import observation_report
from contacompa.application.services.records import (
    CorrectionConflictError,
    InvalidCorrectionError,
    NotFoundError,
    ObservationFilter,
)
from contacompa.entrypoints.api.deps import BlobsDep, CompanyDep, SessionDep
from contacompa.entrypoints.api.schemas import (
    CorrectionRequest,
    ObservationReportOut,
    PurchaseDocDetail,
    PurchaseDocList,
    PurchaseDocSummary,
)

router = APIRouter(prefix="/v1/purchase-docs", tags=["purchase docs"])
reports = APIRouter(prefix="/v1/reports", tags=["purchase docs"])


@router.get("")
async def list_purchase_docs(
    session: SessionDep,
    company: CompanyDep,
    observations: Annotated[ObservationFilter, Query()] = "all",
    code: Annotated[str | None, Query(max_length=64)] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PurchaseDocList:
    """observations: all · any (at least one) · warning (a value may be wrong) · none (clean)."""
    rows = await records.list_purchase_docs(
        session,
        company.id,
        observations=observations,
        code=code,
        date_from=date_from,
        date_to=date_to,
        limit=limit + 1,
        offset=offset,
    )
    page = rows[:limit]
    lines = await records.lines_by_doc(session, [doc.id for doc, _ in page])
    return PurchaseDocList(
        items=[PurchaseDocSummary.build(doc, supplier, lines[doc.id]) for doc, supplier in page],
        next_offset=offset + limit if len(rows) > limit else None,
    )


@router.get("/{purchase_doc_id}")
async def get_purchase_doc(
    purchase_doc_id: UUID, session: SessionDep, company: CompanyDep
) -> PurchaseDocDetail:
    try:
        view = await records.get_purchase_doc(session, company.id, purchase_doc_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return PurchaseDocDetail.from_view(view)


@router.patch("/{purchase_doc_id}")
async def correct_purchase_doc(
    purchase_doc_id: UUID, body: CorrectionRequest, session: SessionDep, company: CompanyDep
) -> PurchaseDocDetail:
    """Change values the model got wrong; each change is logged and observations recomputed."""
    try:
        view = await records.correct(
            session,
            company,
            purchase_doc_id,
            fields=body.fields.model_dump(exclude_unset=True, mode="json"),
            lines=[line.model_dump(exclude_unset=True, mode="json") for line in body.lines],
            reviewer=body.reviewer,
        )
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InvalidCorrectionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except CorrectionConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return PurchaseDocDetail.from_view(view)


@router.delete("/{purchase_doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_purchase_doc(
    purchase_doc_id: UUID, session: SessionDep, blobs: BlobsDep, company: CompanyDep
) -> None:
    """Erase the purchase doc and its files, jobs and raw results; the file can be re-uploaded."""
    try:
        await records.delete_purchase_doc(session, blobs, company.id, purchase_doc_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@reports.get("/observations")
async def get_observation_report(
    session: SessionDep,
    company: CompanyDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> ObservationReportOut:
    """How many purchase docs are clean, how many have warnings, and each observation's count."""
    report = await observation_report(session, company.id, date_from, date_to)
    return ObservationReportOut.build(report)
