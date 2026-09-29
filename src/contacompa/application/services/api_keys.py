"""Mint API keys: the secret is returned once and only its hash is stored."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from contacompa.domain.api_key import (
    DEFAULT_TTL_HOURS,
    expiry_from,
    generate_api_key,
    hash_api_key,
)
from contacompa.infrastructure.db import repos


@dataclass(frozen=True)
class MintedKey:
    api_key: str
    company_ruc: str
    expires_at: datetime


async def mint_api_key(
    session: AsyncSession, company_ruc: str, hours: int = DEFAULT_TTL_HOURS
) -> MintedKey:
    """Issue a key valid for `hours` to a seeded company. LookupError for an unknown RUC."""
    company = await repos.get_company_by_ruc(session, company_ruc)
    if company is None:
        raise LookupError("unknown company RUC")
    api_key = generate_api_key()
    expires_at = expiry_from(datetime.now(UTC), hours)
    await repos.create_api_key(
        session, company_id=company.id, key_hash=hash_api_key(api_key), expires_at=expires_at
    )
    await session.commit()
    return MintedKey(api_key=api_key, company_ruc=company.ruc, expires_at=expires_at)
