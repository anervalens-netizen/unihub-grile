"""Runtime configuration loaded from environment with safe defaults.

Standalone development remains deterministic, while production-capable settings
must make identity and external-provider boundaries explicit. Unsafe production
combinations are rejected while settings are constructed, before API/worker
startup can advertise readiness.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator
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
    worker_lease_seconds: int = Field(
        default=1800,
        ge=30,
        validation_alias="UGRILE_WORKER_LEASE_SECONDS",
        description=(
            "RUNNING job lease timeout. A committed claim older than this can be "
            "recovered after a worker/process crash."
        ),
    )

    # Export artifact retention. The cleaner is intentionally explicit and
    # only operates inside the managed ``ugrile-s5-exports`` root. Age and
    # operation-count caps are both enforced so disk growth remains bounded.
    export_artifact_retention_hours: int = Field(
        default=168,
        ge=1,
        validation_alias="UGRILE_EXPORT_RETENTION_HOURS",
        description="Maximum age of managed export operation artifacts.",
    )
    export_artifact_max_operations: int = Field(
        default=500,
        ge=1,
        validation_alias="UGRILE_EXPORT_MAX_OPERATIONS",
        description="Maximum number of managed export operation entries retained.",
    )

    # Connector / fixture ingestion. Retail remains read-only during the
    # standalone plugin-candidate program.
    connector_default: str = "fixture-v1"

    # Google provider contract. Fake/local is the safe default. Selecting live
    # never authorizes a mutation by itself: live writes also require an
    # explicit mutation opt-in and an external credentials file path.
    google_provider: Literal["fake", "live"] = Field(
        default="fake",
        validation_alias="UGRILE_GOOGLE_PROVIDER",
        description="Google projection provider. 'fake' performs no network I/O.",
    )
    google_credentials_file: str | None = Field(
        default=None,
        validation_alias="UGRILE_GOOGLE_CREDENTIALS_FILE",
        repr=False,
        description=(
            "Path to an externally mounted Google credential file. Credential JSON "
            "must never be provided inline or committed to the repository."
        ),
    )
    google_live_mutations_enabled: bool = Field(
        default=False,
        validation_alias="UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED",
        description="Explicit second gate for live Google mutations.",
    )

    @model_validator(mode="after")
    def validate_runtime_safety(self) -> Self:
        """Reject configurations that could falsely look production-safe."""

        credentials = self.google_credentials_file
        if self.google_live_mutations_enabled:
            if self.google_provider != "live":
                raise ValueError(
                    "live Google mutations require UGRILE_GOOGLE_PROVIDER=live"
                )
            if not credentials:
                raise ValueError(
                    "live Google mutations require UGRILE_GOOGLE_CREDENTIALS_FILE"
                )

        if (
            self.google_provider == "live"
            and credentials
            and not Path(credentials).is_absolute()
        ):
            raise ValueError("UGRILE_GOOGLE_CREDENTIALS_FILE must be an absolute path")

        if self.app_env != "prod":
            return self

        violations: list[str] = []
        if self.identity_provider == "dev_headers":
            violations.append("IDENTITY_PROVIDER=dev_headers is forbidden in prod")
        else:
            # ``external`` is a reserved interface only. Allowing prod startup
            # before a concrete trusted adapter is mounted would let /readyz be
            # green while every business request fails authentication.
            violations.append("IDENTITY_PROVIDER=external is not implemented for prod")
        if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            violations.append("prod requires PostgreSQL DATABASE_URL")
        if "://grile:grile@" in self.database_url:
            violations.append("development database credentials are forbidden in prod")
        if self.database_echo:
            violations.append("DATABASE_ECHO must be false in prod")
        if self.google_provider != "live":
            violations.append("UGRILE_GOOGLE_PROVIDER=fake is forbidden in prod")
        if not credentials:
            violations.append("prod live Google provider requires a credential file path")
        elif not Path(credentials).is_absolute():
            violations.append("prod Google credential file path must be absolute")

        if violations:
            raise ValueError("unsafe prod configuration: " + "; ".join(violations))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance."""

    return Settings()
