"""Authenticated Google Sheets REST transport for the live projection provider.

This module owns only service-account authentication and bounded HTTP calls. It
never decides which spreadsheet a store owns and never contains business rules.
Those remain in the provider/service layers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from urllib.parse import quote

import requests
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from ..domain.errors import DomainError
from .google import GoogleAdapterError

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
DEFAULT_TIMEOUT_SECONDS = 30.0


class GoogleRetryableTransportError(GoogleAdapterError):
    """Network/quota/server failure safe for the durable worker to retry."""


class GoogleTerminalTransportError(DomainError):
    """Provider request rejected in a way that requires operator remediation."""


class GoogleSheetsTransport(Protocol):
    """Small transport seam used by the live provider and unit tests."""

    def existing_row_count(self, spreadsheet_id: str, range_a1: str) -> int:
        """Return the number of non-empty rows currently reported for a range."""
        ...

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        """Write multiple value ranges in one Sheets API request."""
        ...


class GoogleSheetsApiTransport:
    """Real Sheets v4 transport authenticated with a service-account file."""

    def __init__(
        self,
        authorized_session: AuthorizedSession,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._session = authorized_session
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_service_account_file(cls, credentials_file: str) -> GoogleSheetsApiTransport:
        """Build an authorized transport from an externally mounted key file."""

        try:
            credentials = service_account.Credentials.from_service_account_file(
                credentials_file,
                scopes=[SHEETS_SCOPE],
            )
        except (ValueError, OSError, GoogleAuthError) as exc:
            raise GoogleTerminalTransportError(
                "Google service-account credentials could not be loaded",
                details={"code": "GOOGLE_CREDENTIALS_INVALID"},
            ) from exc
        return cls(AuthorizedSession(credentials))

    def existing_row_count(self, spreadsheet_id: str, range_a1: str) -> int:
        url = (
            f"{SHEETS_API_BASE}/{quote(spreadsheet_id, safe='')}/values/"
            f"{quote(range_a1, safe='')}"
        )
        payload = self._request_json("GET", url)
        values = payload.get("values", [])
        if not isinstance(values, list):
            raise GoogleRetryableTransportError(
                "Google Sheets returned an invalid values payload",
                details={"code": "GOOGLE_LIVE_RESPONSE_INVALID"},
            )
        return len(values)

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        url = f"{SHEETS_API_BASE}/{quote(spreadsheet_id, safe='')}/values:batchUpdate"
        return self._request_json(
            "POST",
            url,
            json_body={
                "valueInputOption": "RAW",
                "data": [dict(item) for item in data],
            },
        )

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        json_body: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        try:
            response = self._session.request(
                method,
                url,
                json=dict(json_body) if json_body is not None else None,
                timeout=self._timeout_seconds,
            )
        except (requests.RequestException, GoogleAuthError, TimeoutError, OSError) as exc:
            raise GoogleRetryableTransportError(
                "Google Sheets transport failed before receiving a response",
                details={"code": "GOOGLE_LIVE_TRANSPORT_ERROR"},
            ) from exc

        status_code = int(response.status_code)
        if status_code >= 400:
            details = {
                "code": "GOOGLE_LIVE_HTTP_ERROR",
                "status_code": status_code,
            }
            if status_code in {408, 429} or status_code >= 500:
                raise GoogleRetryableTransportError(
                    "Google Sheets returned a retryable HTTP response",
                    details=details,
                )
            raise GoogleTerminalTransportError(
                "Google Sheets rejected the request",
                details=details,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise GoogleRetryableTransportError(
                "Google Sheets returned a non-JSON response",
                details={"code": "GOOGLE_LIVE_RESPONSE_INVALID"},
            ) from exc
        if not isinstance(payload, Mapping):
            raise GoogleRetryableTransportError(
                "Google Sheets returned an invalid response shape",
                details={"code": "GOOGLE_LIVE_RESPONSE_INVALID"},
            )
        return payload


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "GoogleRetryableTransportError",
    "GoogleSheetsApiTransport",
    "GoogleSheetsTransport",
    "GoogleTerminalTransportError",
    "SHEETS_SCOPE",
]
