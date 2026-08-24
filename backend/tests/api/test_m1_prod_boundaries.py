from __future__ import annotations

import pytest
from pydantic import ValidationError

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


def test_prod_startup_fails_before_fixture_ingest_can_mount(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="unsafe prod configuration"):
            _paths()
    finally:
        get_settings.cache_clear()
