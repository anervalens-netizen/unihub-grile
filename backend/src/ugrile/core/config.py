"""Runtime configuration loaded from environment with safe defaults.

Settings are kept tiny and explicit so S1 has no implicit environment coupling.
Tests inject settings via the ``get_settings`` cache or the ``DATABASE_URL``
environment variable.
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

    # Worker / job queue settings.
    worker_enabled: bool = True
    worker_poll_seconds: float = 0.5

    # Connector / fixture ingestion. No live retail import is enabled at this stage.
    connector_default: str = "fixture-v1"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance."""

    return Settings()
