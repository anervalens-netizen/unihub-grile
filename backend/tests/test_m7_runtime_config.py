from __future__ import annotations

import pytest
from pydantic import ValidationError

from ugrile.core.config import Settings

DEV_DATABASE_URL = "postgresql+psycopg://grile:grile@127.0.0.1:5432/grile"
TEST_DATABASE_URL = "sqlite+pysqlite:///:memory:"


def test_dev_defaults_remain_safe_and_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", DEV_DATABASE_URL)
    monkeypatch.setenv("IDENTITY_PROVIDER", "dev_headers")
    monkeypatch.setenv("UGRILE_GOOGLE_PROVIDER", "fake")
    monkeypatch.setenv("UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED", "false")
    monkeypatch.delenv("UGRILE_GOOGLE_CREDENTIALS_FILE", raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_env == "dev"
    assert settings.identity_provider == "dev_headers"
    assert settings.google_provider == "fake"
    assert settings.google_live_mutations_enabled is False


def test_live_mutation_gate_requires_live_provider_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("IDENTITY_PROVIDER", "dev_headers")
    monkeypatch.setenv("UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED", "true")
    monkeypatch.delenv("UGRILE_GOOGLE_CREDENTIALS_FILE", raising=False)

    monkeypatch.setenv("UGRILE_GOOGLE_PROVIDER", "fake")
    with pytest.raises(ValidationError, match="UGRILE_GOOGLE_PROVIDER=live"):
        Settings(_env_file=None)

    monkeypatch.setenv("UGRILE_GOOGLE_PROVIDER", "live")
    with pytest.raises(ValidationError, match="UGRILE_GOOGLE_CREDENTIALS_FILE"):
        Settings(_env_file=None)

    monkeypatch.setenv("UGRILE_GOOGLE_CREDENTIALS_FILE", "relative/google.json")
    with pytest.raises(ValidationError, match="absolute path"):
        Settings(_env_file=None)


def test_prod_rejects_development_identity_database_and_fake_google(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DATABASE_URL", DEV_DATABASE_URL)
    monkeypatch.setenv("DATABASE_ECHO", "false")
    monkeypatch.setenv("IDENTITY_PROVIDER", "dev_headers")
    monkeypatch.setenv("UGRILE_GOOGLE_PROVIDER", "fake")
    monkeypatch.setenv("UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED", "false")
    monkeypatch.delenv("UGRILE_GOOGLE_CREDENTIALS_FILE", raising=False)

    with pytest.raises(ValidationError) as captured:
        Settings(_env_file=None)

    message = str(captured.value)
    assert "dev_headers is forbidden in prod" in message
    assert "development database credentials are forbidden in prod" in message
    assert "UGRILE_GOOGLE_PROVIDER=fake is forbidden in prod" in message


def test_prod_rejects_non_postgres_and_database_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///unsafe.db")
    monkeypatch.setenv("DATABASE_ECHO", "true")
    monkeypatch.setenv("IDENTITY_PROVIDER", "external")
    monkeypatch.setenv("UGRILE_GOOGLE_PROVIDER", "live")
    monkeypatch.setenv("UGRILE_GOOGLE_CREDENTIALS_FILE", "/run/secrets/google.json")
    monkeypatch.setenv("UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED", "false")

    with pytest.raises(ValidationError) as captured:
        Settings(_env_file=None)

    message = str(captured.value)
    assert "prod requires PostgreSQL DATABASE_URL" in message
    assert "DATABASE_ECHO must be false in prod" in message


def test_explicit_prod_safe_shape_passes_startup_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://grile_app:nondefault@db:5432/grile",
    )
    monkeypatch.setenv("DATABASE_ECHO", "false")
    monkeypatch.setenv("IDENTITY_PROVIDER", "external")
    monkeypatch.setenv("UGRILE_GOOGLE_PROVIDER", "live")
    monkeypatch.setenv(
        "UGRILE_GOOGLE_CREDENTIALS_FILE",
        "/run/secrets/google-service-account.json",
    )
    monkeypatch.setenv("UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED", "false")

    settings = Settings(_env_file=None)

    assert settings.app_env == "prod"
    assert settings.identity_provider == "external"
    assert settings.google_provider == "live"
