from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.postgres import PostgresContainer

from contacompa.config import get_settings
from contacompa.domain.api_key import expiry_from, hash_api_key
from contacompa.domain.purchase_doc import SourceKind
from contacompa.infrastructure.db.engine import make_engine, make_session_factory
from contacompa.infrastructure.db.models import Company, Document
from contacompa.infrastructure.db.repos import (
    create_api_key,
    ensure_company,
    get_company_by_api_key,
    get_company_by_ruc,
)

TABLES = (
    "corrections, purchase_doc_lines, purchase_docs, extraction_results, eval_results, "
    "eval_runs, jobs, documents, suppliers, api_keys, companies, daily_spend"
)
COMPANY_RUC = "20543306771"


async def make_company(session: AsyncSession, api_key: str = "test-key") -> Company:
    """The seeded company with a live test API key (the API creates the company at startup too)."""
    await ensure_company(session, ruc=COMPANY_RUC, legal_name="CIMMSA")
    company = await get_company_by_ruc(session, COMPANY_RUC)
    assert company is not None
    now = datetime.now(UTC)
    if await get_company_by_api_key(session, api_key, now) is None:
        await create_api_key(
            session,
            company_id=company.id,
            key_hash=hash_api_key(api_key),
            expires_at=expiry_from(now),
        )
    await session.commit()
    return company


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    with PostgresContainer("postgres:17-alpine", driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture
def migrated(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Schema at head. Cheap when already migrated; restores tables after test_migrations
    downgrades to base."""
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("BLOB_DIR", "/tmp/blobs")  # noqa: S108 — test-only path
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    yield
    get_settings.cache_clear()


@pytest.fixture
async def sessions(pg_url: str, migrated: None) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(pg_url)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))
    try:
        yield make_session_factory(engine)
    finally:
        await engine.dispose()


def make_document_row(company: Company, sha256: str, storage_key: str, size: int) -> Document:
    return Document(
        company_id=company.id,
        sha256=sha256,
        filename="x.pdf",
        mime_type="application/pdf",
        source_kind=SourceKind.PDF_TEXT,
        size_bytes=size,
        storage_key=storage_key,
    )
