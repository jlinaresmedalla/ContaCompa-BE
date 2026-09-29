from decimal import Decimal
from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # required
    database_url: str
    api_key: SecretStr
    blob_dir: str

    # the company the configured API key belongs to (seeded at startup; more companies via SQL)
    company_ruc: str = "20543306771"
    company_name: str = "CORPORACION CIMMSA S.A."

    # providers: only adapters that exist get a key here (others arrive with their adapter)
    google_api_key: SecretStr | None = None

    # default run configuration
    default_provider: str = "gemini"
    default_model: str = "gemini-3.5-flash-lite"
    default_parser: str = "native"

    # tracing / telemetry
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "contacompa"
    otel_service_name: str = "contacompa"
    otel_exporter_otlp_endpoint: str | None = None

    # origins allowed to call the API from a browser (the web/ dashboard)
    cors_origins: list[str] = ["http://localhost:5173"]

    # limits and operations
    daily_cost_ceiling_usd: Decimal = Decimal("1.00")
    worker_concurrency: int = 2
    worker_lease_seconds: int = 300
    max_upload_bytes: int = 10 * 1024 * 1024
    max_pages: int = 10

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, raw: str) -> str:
        """Accept Neon's libpq URL and use TLS verification with the asyncpg dialect."""
        url = make_url(raw)
        if url.drivername in {"postgres", "postgresql"}:
            url = url.set(drivername="postgresql+asyncpg")
        if url.drivername == "postgresql+asyncpg":
            query = dict(url.query)
            sslmode = query.pop("sslmode", None)
            query.pop("channel_binding", None)
            if sslmode:
                query["ssl"] = "verify-full" if sslmode == "require" else sslmode
            url = url.set(query=query)
        return url.render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
