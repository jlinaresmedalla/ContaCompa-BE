from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from contacompa.config import Settings
from contacompa.domain.api_key import admin_key_matches
from contacompa.infrastructure.blob import BlobStore
from contacompa.infrastructure.db.models import Company
from contacompa.infrastructure.db.repos import get_company_by_api_key


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_blobs(request: Request) -> BlobStore:
    blobs: BlobStore = request.app.state.blobs
    return blobs


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessions() as session:
        yield session


# Security schemes (not plain headers) so Swagger UI shows an "Authorize" button for each.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


@dataclass(frozen=True)
class SignedIn:
    company: Company
    expires_at: datetime


async def get_signed_in(
    request: Request, x_api_key: Annotated[str | None, Security(api_key_header)] = None
) -> SignedIn:
    """The API key identifies the company; every request is scoped to it. Expired keys are 401."""
    if x_api_key:
        async with request.app.state.sessions() as session:
            found = await get_company_by_api_key(session, x_api_key, datetime.now(UTC))
        if found is not None:
            return SignedIn(company=found[0], expires_at=found[1])
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid API key")


async def get_company(signed_in: Annotated[SignedIn, Depends(get_signed_in)]) -> Company:
    return signed_in.company


async def require_admin_key(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    x_admin_key: Annotated[str | None, Security(admin_key_header)] = None,
) -> None:
    configured = settings.admin_api_key.get_secret_value() if settings.admin_api_key else None
    if not admin_key_matches(x_admin_key, configured):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid admin key")


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
BlobsDep = Annotated[BlobStore, Depends(get_blobs)]
CompanyDep = Annotated[Company, Depends(get_company)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SignedInDep = Annotated[SignedIn, Depends(get_signed_in)]
