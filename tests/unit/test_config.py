from collections.abc import Generator
from decimal import Decimal

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from contacompa.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_cache() -> Generator[None]:
    """Clear the get_settings cache before each test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_missing_required_settings(monkeypatch: MonkeyPatch) -> None:
    """Missing required settings → ValidationError names database_url, api_key, blob_dir."""
    # Clear all environment variables that could provide the settings
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("BLOB_DIR", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    error_message = str(exc_info.value)
    assert "database_url" in error_message
    assert "api_key" in error_message
    assert "blob_dir" in error_message


def test_with_required_settings(monkeypatch: MonkeyPatch) -> None:
    """With DATABASE_URL, API_KEY, BLOB_DIR set → constructs; defaults hold."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("BLOB_DIR", "/tmp/blobs")  # noqa: S108

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+asyncpg://localhost/test"
    assert settings.blob_dir == "/tmp/blobs"  # noqa: S108
    assert settings.default_provider == "gemini"
    assert settings.worker_concurrency == 2
    assert settings.daily_cost_ceiling_usd == Decimal("1.00")
    assert settings.max_upload_bytes == 10485760


def test_worker_concurrency_and_cost_ceiling_parsing(monkeypatch: MonkeyPatch) -> None:
    """WORKER_CONCURRENCY=5 and DAILY_COST_CEILING_USD=0.25 parse to int/Decimal."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("BLOB_DIR", "/tmp/blobs")  # noqa: S108
    monkeypatch.setenv("WORKER_CONCURRENCY", "5")
    monkeypatch.setenv("DAILY_COST_CEILING_USD", "0.25")

    settings = Settings(_env_file=None)

    assert settings.worker_concurrency == 5
    assert isinstance(settings.worker_concurrency, int)
    assert settings.daily_cost_ceiling_usd == Decimal("0.25")
    assert isinstance(settings.daily_cost_ceiling_usd, Decimal)


def test_api_key_is_secret_str(monkeypatch: MonkeyPatch) -> None:
    """api_key is a SecretStr: str() does not contain raw value; .get_secret_value() does."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("API_KEY", "super-secret-key")
    monkeypatch.setenv("BLOB_DIR", "/tmp/blobs")  # noqa: S108

    settings = Settings(_env_file=None)

    # str() should not contain the raw value
    string_repr = str(settings.api_key)
    assert "super-secret-key" not in string_repr

    # .get_secret_value() should return the raw value
    assert settings.api_key.get_secret_value() == "super-secret-key"


def test_get_settings_cached(monkeypatch: MonkeyPatch) -> None:
    """get_settings() returns the same object twice (cached)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("API_KEY", "test-api-key")
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
        api_key="test-api-key",
        blob_dir="/tmp/blobs",  # noqa: S108
    )
    assert settings.database_url == (
        "postgresql+asyncpg://user:p%40ss@ep-demo.neon.tech/demo?ssl=verify-full"
    )
