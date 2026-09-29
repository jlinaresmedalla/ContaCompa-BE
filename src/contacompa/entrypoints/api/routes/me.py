from fastapi import APIRouter

from contacompa.entrypoints.api.deps import SignedInDep
from contacompa.entrypoints.api.schemas import CompanyOut, MeOut

router = APIRouter(prefix="/v1", tags=["me"])


@router.get("/me")
async def get_me(signed_in: SignedInDep) -> MeOut:
    """The company the presented API key belongs to and when that key expires."""
    company = signed_in.company
    return MeOut(
        company=CompanyOut(ruc=company.ruc, legal_name=company.legal_name),
        expires_at=signed_in.expires_at,
    )
