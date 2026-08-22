"""Runtime configuration loaded from environment with safe defaults.

Standalone development remains deterministic, while production-capable settings
must make identity and fixture boundaries explicit.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Top-level application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "ugrile-backend"
    app_env: Literal["dev", "test", "ci", "prod"] = "dev"
    log_level: str = "INFO"
    timezone: str = "Europe/Bucharest"

    database_url: str = Field(
        default="postgresql+psycopg://grile:grile@127.0.0.1:5432/grile",
        description="SQLAlchemy URL. Tests override with sqlite+pysqlite:///:memory:.",
    )
    database_echo: bool = False

    # Identity seam. Only the explicit development-header provider exists in
    # the standalone application today. ``external`` reserves the contract for
    # the future Retail identity adapter; until implemented it fails closed.
    identity_provider: Literal["dev_headers", "external"] = "dev_headers"

    # Worker / job queue settings.
    worker_enabled: bool = True
    worker_poll_seconds: float = Field(
        default=0.5,
        validation_alias="UGRILE_WORKER_POLL_SECONDS",
        description="Outbox poll interval for the durable worker loop.",
    )

    # Connector / fixture ingestion. Retail remains read-only during the
    # standalone plugin-candidate program.
    connector_default: str = "fixture-v1"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance."""

    return Settings()
