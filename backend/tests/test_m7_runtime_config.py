from __future__ import annotations

import pytest
from pydantic import ValidationError

from ugrile.core.config import Settings


def test_dev_defaults_remain_safe_and_usable() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "dev"
    assert settings.identity_provider == "dev_headers"
    assert settings.google_provider == "fake"
    assert settings.google_live_mutations_enabled is False


def test_live_mutation_gate_requires_live_provider_and_credentials() -> None:
    with pytest.raises(ValidationError, match="UGRILE_GOOGLE_PROVIDER=live"):
        Settings(_env_file=None, google_live_mutations_enabled=True)

    with pytest.raises(ValidationError, match="UGRILE_GOOGLE_CREDENTIALS_FILE"):
        Settings(
            _env_file=None,
            google_provider="live",
            google_live_mutations_enabled=True,
        )

    with pytest.raises(ValidationError, match="absolute path"):
        Settings(
            _env_file=None,
            google_provider="live",
            google_credentials_file="relative/google.json",
            google_live_mutations_enabled=True,
        )


def test_prod_rejects_development_identity_database_and_fake_google() -> None:
    with pytest.raises(ValidationError) as captured:
        Settings(_env_file=None, app_env="prod")

    message = str(captured.value)
    assert "dev_headers is forbidden in prod" in message
    assert "development database credentials are forbidden in prod" in message
    assert "UGRILE_GOOGLE_PROVIDER=fake is forbidden in prod" in message


def test_prod_rejects_non_postgres_and_database_echo() -> None:
    with pytest.raises(ValidationError) as captured:
        Settings(
            _env_file=None,
            app_env="prod",
            identity_provider="external",
            database_url="sqlite+pysqlite:///unsafe.db",
            database_echo=True,
            google_provider="live",
            google_credentials_file="/run/secrets/google.json",
        )

    message = str(captured.value)
    assert "prod requires PostgreSQL DATABASE_URL" in message
    assert "DATABASE_ECHO must be false in prod" in message


def test_explicit_prod_safe_shape_passes_startup_validation() -> None:
    settings = Settings(
        _env_file=None,
        app_env="prod",
        identity_provider="external",
        database_url="postgresql+psycopg://grile_app:nondefault@db:5432/grile",
        database_echo=False,
        google_provider="live",
        google_credentials_file="/run/secrets/google-service-account.json",
        google_live_mutations_enabled=False,
    )

    assert settings.app_env == "prod"
    assert settings.identity_provider == "external"
    assert settings.google_provider == "live"
