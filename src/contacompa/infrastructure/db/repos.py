import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from contacompa.domain.purchase_doc import SourceKind
from contacompa.infrastructure.db.models import (
    Company,
    DailySpend,
    Document,
    ExtractionResult,
    Job,
)


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


async def get_company_by_api_key(session: AsyncSession, api_key: str) -> Company | None:
    stmt = select(Company).where(Company.api_key_hash == hash_api_key(api_key))
    return (await session.execute(stmt)).scalar_one_or_none()


async def ensure_company(session: AsyncSession, *, ruc: str, legal_name: str, api_key: str) -> None:
    """Create the configured company, or point it at the configured API key."""
    stmt = insert(Company).values(
        id=uuid.uuid4(), ruc=ruc, legal_name=legal_name, api_key_hash=hash_api_key(api_key)
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Company.ruc], set_={"api_key_hash": stmt.excluded.api_key_hash}
    )
    await session.execute(stmt)


async def get_document_by_sha(
    session: AsyncSession, company_id: UUID, sha256: str
) -> Document | None:
    stmt = select(Document).where(Document.company_id == company_id, Document.sha256 == sha256)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_document(session: AsyncSession, document_id: UUID) -> Document | None:
    return await session.get(Document, document_id)


async def create_document(
    session: AsyncSession,
    *,
    company_id: UUID,
    sha256: str,
    filename: str,
    mime_type: str,
    source_kind: SourceKind,
    size_bytes: int,
    page_count: int | None,
    width_px: int | None,
    height_px: int | None,
    storage_key: str,
) -> Document:
    document = Document(
        company_id=company_id,
        sha256=sha256,
        filename=filename,
        mime_type=mime_type,
        source_kind=source_kind,
        size_bytes=size_bytes,
        page_count=page_count,
        width_px=width_px,
        height_px=height_px,
        storage_key=storage_key,
    )
    session.add(document)
    await session.flush()
    return document


async def get_job(session: AsyncSession, job_id: UUID) -> Job | None:
    return await session.get(Job, job_id)


async def latest_job_for_document(session: AsyncSession, document_id: UUID) -> Job | None:
    stmt = (
        select(Job).where(Job.document_id == document_id).order_by(Job.created_at.desc()).limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_result_for_document(
    session: AsyncSession, document_id: UUID
) -> ExtractionResult | None:
    stmt = (
        select(ExtractionResult)
        .where(ExtractionResult.document_id == document_id)
        .order_by(ExtractionResult.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def add_daily_spend(session: AsyncSession, amount: Decimal, day: date | None = None) -> None:
    key = day or datetime.now(UTC).date()
    stmt = insert(DailySpend).values(day=key, cost_usd=amount)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DailySpend.day], set_={"cost_usd": DailySpend.cost_usd + amount}
    )
    await session.execute(stmt)


async def daily_spend(session: AsyncSession, day: date | None = None) -> Decimal:
    key = day or datetime.now(UTC).date()
    row = await session.get(DailySpend, key)
    return row.cost_usd if row is not None else Decimal("0")
