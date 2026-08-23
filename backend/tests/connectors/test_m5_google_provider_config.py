"""M5 Google provider and credential-boundary tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from ugrile.connectors.google import (
    GoogleAdapterError,
    StoreProjection,
    read_store_projection,
)
from ugrile.connectors.google_provider import (
    FakeGoogleProjectionProvider,
    build_google_projection_provider,
)
from ugrile.core.config import Settings
from ugrile.services.google import GoogleProjectionService

GOOGLE_ENV_KEYS = (
    "UGRILE_GOOGLE_PROVIDER",
    "UGRILE_GOOGLE_CREDENTIALS_FILE",
    "UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED",
)


def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key in GOOGLE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return Settings(_env_file=None)


def test_google_provider_defaults_to_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)

    assert settings.google_provider == "fake"
    assert settings.google_live_mutations_enabled is False
    assert settings.google_credentials_file is None
    assert isinstance(build_google_projection_provider(settings), FakeGoogleProjectionProvider)


def test_live_provider_requires_explicit_mutation_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch)
    monkeypatch.setenv("UGRILE_GOOGLE_PROVIDER", "live")
    settings = Settings(_env_file=None)

    with pytest.raises(GoogleAdapterError) as excinfo:
        build_google_projection_provider(settings)

    assert excinfo.value.details == {"code": "GOOGLE_LIVE_MUTATIONS_DISABLED"}


def test_live_provider_requires_external_credentials_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch)
    monkeypatch.setenv("UGRILE_GOOGLE_PROVIDER", "live")
    monkeypatch.setenv("UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED", "true")
    settings = Settings(_env_file=None)

    with pytest.raises(GoogleAdapterError) as excinfo:
        build_google_projection_provider(settings)

    assert excinfo.value.details == {"code": "GOOGLE_CREDENTIALS_FILE_REQUIRED"}


def test_live_provider_rejects_missing_credentials_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _settings(monkeypatch)
    missing = tmp_path / "missing-service-account.json"
    monkeypatch.setenv("UGRILE_GOOGLE_PROVIDER", "live")
    monkeypatch.setenv("UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED", "true")
    monkeypatch.setenv("UGRILE_GOOGLE_CREDENTIALS_FILE", str(missing))
    settings = Settings(_env_file=None)

    with pytest.raises(GoogleAdapterError) as excinfo:
        build_google_projection_provider(settings)

    assert excinfo.value.details == {"code": "GOOGLE_CREDENTIALS_FILE_UNAVAILABLE"}


def test_live_config_never_reads_or_exposes_credential_contents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _settings(monkeypatch)
    secret = "PRIVATE_KEY_MATERIAL_MUST_NOT_LEAK"
    credentials = tmp_path / "service-account.json"
    credentials.write_text(secret, encoding="utf-8")
    monkeypatch.setenv("UGRILE_GOOGLE_PROVIDER", "live")
    monkeypatch.setenv("UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED", "true")
    monkeypatch.setenv("UGRILE_GOOGLE_CREDENTIALS_FILE", str(credentials))
    settings = Settings(_env_file=None)

    assert secret not in repr(settings)
    assert str(credentials) not in repr(settings)
    with pytest.raises(GoogleAdapterError) as excinfo:
        build_google_projection_provider(settings)

    assert excinfo.value.details == {"code": "GOOGLE_LIVE_TRANSPORT_UNAVAILABLE"}
    assert secret not in str(excinfo.value)
    assert secret not in repr(excinfo.value.details)


def test_fake_provider_preserves_existing_projection_semantics(
    monkeypatch: pytest.MonkeyPatch,
    session,
    faker_tenant,
) -> None:
    settings = _settings(monkeypatch)
    provider = build_google_projection_provider(settings)
    payload = {
        "grila": {"revision": 7, "rows": []},
        "pontaj": {"revision": 7, "rows": []},
    }

    projection = provider.write_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        generation="FIXTURE_V1",
        payload=payload,
    )
    session.commit()

    assert projection.generation == "FIXTURE_V1"
    persisted = read_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
    )
    assert persisted is not None
    assert persisted.grila["revision"] == 7
    assert persisted.pontaj["revision"] == 7


def test_projection_service_uses_injected_provider_seam(session, faker_tenant) -> None:
    class RecordingProvider:
        name = "recording"

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def write_store_projection(
            self,
            session,
            *,
            tenant_id: str,
            store_id: str,
            generation: str,
            payload: Mapping[str, Any],
        ) -> StoreProjection:
            self.calls.append(
                {
                    "tenant_id": tenant_id,
                    "store_id": store_id,
                    "generation": generation,
                    "payload": payload,
                }
            )
            grila = payload["grila"]
            pontaj = payload["pontaj"]
            assert isinstance(grila, Mapping)
            assert isinstance(pontaj, Mapping)
            return StoreProjection(
                store_id=store_id,
                generation=generation,
                grila=grila,
                pontaj=pontaj,
                last_success_generation=generation,
            )

    provider = RecordingProvider()
    service = GoogleProjectionService(session, provider=provider)

    outcome = service.project_store_for_month(
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id="month_provider_seam",
        year=2026,
        month=8,
        revision=3,
        generation="PROVIDER_SEAM_V1",
    )

    assert outcome.generation == "PROVIDER_SEAM_V1"
    assert len(provider.calls) == 1
    assert provider.calls[0]["tenant_id"] == faker_tenant["tenant_id"]
    assert provider.calls[0]["store_id"] == faker_tenant["store_id"]
    assert provider.calls[0]["generation"] == "PROVIDER_SEAM_V1"
