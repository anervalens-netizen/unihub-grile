"""M5 Google worker retry classification contracts."""

from ugrile.connectors.google_live import (
    GoogleRetryableTransportError,
    GoogleTerminalTransportError,
)
from ugrile.connectors.google_provider import GoogleProviderConfigurationError
from ugrile.worker.worker import _retryable_exception


def test_google_transport_errors_are_retryable_only_when_typed_retryable() -> None:
    assert _retryable_exception(
        GoogleRetryableTransportError(
            "quota",
            details={"code": "GOOGLE_LIVE_HTTP_ERROR", "status_code": 429},
        )
    )
    assert not _retryable_exception(
        GoogleTerminalTransportError(
            "forbidden",
            details={"code": "GOOGLE_LIVE_HTTP_ERROR", "status_code": 403},
        )
    )
    assert not _retryable_exception(
        GoogleProviderConfigurationError(
            "missing binding",
            details={"code": "GOOGLE_SHEET_BINDING_REQUIRED"},
        )
    )
