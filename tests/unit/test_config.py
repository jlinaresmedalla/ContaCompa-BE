import ssl
from collections.abc import Generator
from decimal import Decimal

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from contacompa.config import Settings, get_settings
from contacompa.infrastructure.db.engine import tls_connect_args


@pytest.fixture(autouse=True)
def _clear_cache() -> Generator[None]:
    """Clear the get_settings cache before each test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_missing_required_settings(monkeypatch: MonkeyPatch) -> None:
    """Missing DATABASE_URL → ValidationError names database_url."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "database_url" in str(exc_info.value)


def test_blob_dir_required_without_s3_bucket(monkeypatch: MonkeyPatch) -> None:
    """Neither BLOB_DIR nor S3_BUCKET → ValidationError says BLOB_DIR is required."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.delenv("BLOB_DIR", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "BLOB_DIR is required" in str(exc_info.value)


def test_s3_bucket_replaces_blob_dir(monkeypatch: MonkeyPatch) -> None:
    """With S3_BUCKET set, BLOB_DIR is optional."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.delenv("BLOB_DIR", raising=False)
    monkeypatch.setenv("S3_BUCKET", "documents")

    settings = Settings(_env_file=None)

    assert settings.s3_bucket == "documents"
    assert settings.blob_dir is None


def test_with_required_settings(monkeypatch: MonkeyPatch) -> None:
    """With DATABASE_URL, BLOB_DIR set → constructs; defaults hold."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("BLOB_DIR", "/tmp/blobs")  # noqa: S108

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+asyncpg://localhost/test"
    assert settings.blob_dir == "/tmp/blobs"  # noqa: S108
    assert settings.default_provider == "gemini"
    assert settings.worker_concurrency == 2
    assert settings.daily_cost_ceiling_usd == Decimal("1.00")
    assert settings.max_upload_bytes == 10485760
    assert settings.admin_api_key is None


def test_worker_concurrency_and_cost_ceiling_parsing(monkeypatch: MonkeyPatch) -> None:
    """WORKER_CONCURRENCY=5 and DAILY_COST_CEILING_USD=0.25 parse to int/Decimal."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("BLOB_DIR", "/tmp/blobs")  # noqa: S108
    monkeypatch.setenv("WORKER_CONCURRENCY", "5")
    monkeypatch.setenv("DAILY_COST_CEILING_USD", "0.25")

    settings = Settings(_env_file=None)

    assert settings.worker_concurrency == 5
    assert isinstance(settings.worker_concurrency, int)
    assert settings.daily_cost_ceiling_usd == Decimal("0.25")
    assert isinstance(settings.daily_cost_ceiling_usd, Decimal)


def test_admin_api_key_is_secret_str(monkeypatch: MonkeyPatch) -> None:
    """admin_api_key is a SecretStr: str() does not contain raw value; .get_secret_value() does."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("ADMIN_API_KEY", "super-secret-key")
    monkeypatch.setenv("BLOB_DIR", "/tmp/blobs")  # noqa: S108

    settings = Settings(_env_file=None)

    # str() should not contain the raw value
    string_repr = str(settings.admin_api_key)
    assert "super-secret-key" not in string_repr

    # .get_secret_value() should return the raw value
    assert settings.admin_api_key is not None
    assert settings.admin_api_key.get_secret_value() == "super-secret-key"


def test_get_settings_cached(monkeypatch: MonkeyPatch) -> None:
    """get_settings() returns the same object twice (cached)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("BLOB_DIR", "/tmp/blobs")  # noqa: S108

    settings1 = get_settings()
    settings2 = get_settings()

    assert settings1 is settings2


def test_neon_url_uses_asyncpg_and_verified_tls() -> None:
    settings = Settings(
        _env_file=None,
        database_url=(
            "postgresql://user:p%40ss@ep-demo.neon.tech/demo"
            "?sslmode=require&channel_binding=require"
        ),
        blob_dir="/tmp/blobs",  # noqa: S108
    )
    assert settings.database_url == (
        "postgresql+asyncpg://user:p%40ss@ep-demo.neon.tech/demo?ssl=verify-full"
    )


def test_verify_full_uses_system_cas_not_root_crt() -> None:
    """verify-full becomes a default SSL context, so asyncpg never looks for root.crt."""
    url, connect_args = tls_connect_args(
        "postgresql+asyncpg://user:p%40ss@ep-demo.neon.tech/demo?ssl=verify-full"
    )
    assert url == "postgresql+asyncpg://user:p%40ss@ep-demo.neon.tech/demo"
    context = connect_args["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


def test_other_ssl_modes_pass_through() -> None:
    raw = "postgresql+asyncpg://user@localhost/test?ssl=require"
    assert tls_connect_args(raw) == (raw, {})
