from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from contacompa.config import Settings
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


# A security scheme (not a plain header) so Swagger UI shows an "Authorize" button.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_company(
    request: Request, x_api_key: Annotated[str | None, Security(api_key_header)] = None
) -> Company:
    """The API key identifies the company; every request is scoped to it."""
    if x_api_key:
        async with request.app.state.sessions() as session:
            company = await get_company_by_api_key(session, x_api_key)
        if company is not None:
            return company
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid API key")


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
BlobsDep = Annotated[BlobStore, Depends(get_blobs)]
CompanyDep = Annotated[Company, Depends(get_company)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
