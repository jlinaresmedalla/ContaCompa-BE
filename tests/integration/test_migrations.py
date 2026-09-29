import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from contacompa.config import get_settings

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "companies",
    "suppliers",
    "purchase_docs",
    "purchase_doc_lines",
    "corrections",
    "documents",
    "jobs",
    "extraction_results",
    "eval_runs",
    "eval_results",
    "daily_spend",
}


async def _table_names(pg_url: str) -> set[str]:
    engine = create_async_engine(pg_url)
    try:
        async with engine.connect() as conn:
            return set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))
    finally:
        await engine.dispose()


def test_upgrade_creates_tables_then_downgrade_leaves_only_alembic_version(
    pg_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("BLOB_DIR", str(tmp_path))
    get_settings.cache_clear()

    cfg = Config("alembic.ini")
    try:
        command.upgrade(cfg, "head")
        tables = asyncio.run(_table_names(pg_url))
        assert EXPECTED_TABLES <= tables

        command.downgrade(cfg, "base")
        tables_after_downgrade = asyncio.run(_table_names(pg_url))
        assert tables_after_downgrade == {"alembic_version"}
    finally:
        get_settings.cache_clear()
