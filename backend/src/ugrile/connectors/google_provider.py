"""Google projection provider selection and fail-closed live configuration.

The current standalone candidate has a deterministic local fake provider. A live
provider is intentionally *not* simulated here: selecting ``live`` validates the
explicit mutation/credential gates and then fails closed until a real transport
implementation lands under GS-001.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session

from ..core.config import Settings, get_settings
from . import google as fake_google
from .google import GoogleAdapterError, StoreProjection


class GoogleProjectionProvider(Protocol):
    """Boundary used by the projection service for external publication."""

    name: str

    def write_store_projection(
        self,
        session: Session,
        *,
        tenant_id: str,
        store_id: str,
        generation: str,
        payload: Mapping[str, Any],
    ) -> StoreProjection:
        """Publish one store projection and return the accepted projection."""


class FakeGoogleProjectionProvider:
    """Deterministic local provider with no network I/O."""

    name = "fake"

    def write_store_projection(
        self,
        session: Session,
        *,
        tenant_id: str,
        store_id: str,
        generation: str,
        payload: Mapping[str, Any],
    ) -> StoreProjection:
        return fake_google.write_store_projection(
            session,
            tenant_id=tenant_id,
            store_id=store_id,
            generation=generation,
            payload=payload,
        )


def build_google_projection_provider(
    settings: Settings | None = None,
) -> GoogleProjectionProvider:
    """Resolve the configured projection provider.

    ``fake`` is immediately usable and performs no network I/O. ``live`` is
    deliberately fail-closed in three layers:

    1. live mutations require an explicit opt-in;
    2. credentials must be supplied by an externally mounted file path;
    3. even with both gates satisfied, no live transport is fabricated. GS-001
       remains open until that real transport exists.

    Credential file contents are never opened or included in error details.
    """

    resolved = settings or get_settings()
    if resolved.google_provider == "fake":
        return FakeGoogleProjectionProvider()

    if not resolved.google_live_mutations_enabled:
        raise GoogleAdapterError(
            "live Google mutations are disabled",
            details={"code": "GOOGLE_LIVE_MUTATIONS_DISABLED"},
        )

    credentials_file = resolved.google_credentials_file
    if not credentials_file:
        raise GoogleAdapterError(
            "live Google provider requires an external credentials file",
            details={"code": "GOOGLE_CREDENTIALS_FILE_REQUIRED"},
        )

    credentials_path = Path(credentials_file).expanduser()
    if not credentials_path.is_file():
        raise GoogleAdapterError(
            "configured Google credentials file is not available",
            details={"code": "GOOGLE_CREDENTIALS_FILE_UNAVAILABLE"},
        )

    raise GoogleAdapterError(
        "live Google projection transport is not implemented",
        details={"code": "GOOGLE_LIVE_TRANSPORT_UNAVAILABLE"},
    )


__all__ = [
    "FakeGoogleProjectionProvider",
    "GoogleProjectionProvider",
    "build_google_projection_provider",
]
