from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import Response

from contacompa.application.services.export import Language, export_xlsx
from contacompa.entrypoints.api.deps import CompanyDep, SessionDep

router = APIRouter(prefix="/v1/exports", tags=["exports"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get(
    "/purchase-docs.xlsx",
    response_class=Response,
    responses={200: {"content": {XLSX_MIME: {}}, "description": "Excel workbook"}},
)
async def export_purchase_docs(
    session: SessionDep,
    company: CompanyDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    lang: Annotated[Language, Query()] = "es",
) -> Response:
    """Purchase docs with issue_date in range: a master-detail report (each purchase doc with its
    lines, prices with and without IGV) and a flat lines sheet. `lang`: es (default) or en."""
    content, count = await export_xlsx(
        session, company.id, company.legal_name, date_from, date_to, lang
    )
    return Response(
        content=content,
        media_type=XLSX_MIME,
        headers={
            "Content-Disposition": 'attachment; filename="purchase-docs.xlsx"',
            "X-Purchase-Doc-Count": str(count),
        },
    )
