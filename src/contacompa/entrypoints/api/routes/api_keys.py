from fastapi import APIRouter, Depends, HTTPException, status

from contacompa.application.services import api_keys
from contacompa.entrypoints.api.deps import SessionDep, require_admin_key
from contacompa.entrypoints.api.schemas import MintedKeyOut, MintKeyIn

router = APIRouter(prefix="/v1/api-keys", tags=["api keys"])


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin_key)])
async def mint_api_key(body: MintKeyIn, session: SessionDep) -> MintedKeyOut:
    """Mint a 12-hour API key for a seeded company. Authorized with `X-Admin-Key`."""
    try:
        minted = await api_keys.mint_api_key(session, body.company_ruc)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return MintedKeyOut(
        api_key=minted.api_key, company_ruc=minted.company_ruc, expires_at=minted.expires_at
    )
