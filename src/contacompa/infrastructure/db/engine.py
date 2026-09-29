import ssl
from typing import Any

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def tls_connect_args(database_url: str) -> tuple[str, dict[str, Any]]:
    """Verify `ssl=verify-full` against the system CAs.

    asyncpg's verify-full otherwise requires ~/.postgresql/root.crt, which the image does not have;
    Neon's certificate chains to a public CA, so the default SSL context verifies it.
    """
    url = make_url(database_url)
    if url.query.get("ssl") != "verify-full" or "sslrootcert" in url.query:
        return database_url, {}
    url = url.difference_update_query(["ssl"])
    return url.render_as_string(hide_password=False), {"ssl": ssl.create_default_context()}


def make_engine(database_url: str, **kwargs: Any) -> AsyncEngine:
    url, connect_args = tls_connect_args(database_url)
    return create_async_engine(url, pool_pre_ping=True, connect_args=connect_args, **kwargs)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
