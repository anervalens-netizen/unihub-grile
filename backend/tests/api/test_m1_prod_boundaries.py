from __future__ import annotations

from ugrile.core.config import get_settings
from ugrile.main import create_app


def _paths() -> set[str]:
    return {route.path for route in create_app().routes}


def test_fixture_ingest_is_mounted_in_test(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    try:
        assert "/ingest/fixture" in _paths()
    finally:
        get_settings.cache_clear()


def test_fixture_ingest_is_not_mounted_in_prod(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    get_settings.cache_clear()
    try:
        assert "/ingest/fixture" not in _paths()
    finally:
        get_settings.cache_clear()
