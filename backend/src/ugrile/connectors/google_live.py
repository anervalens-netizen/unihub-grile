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
_CONTROL_FIELDS = (
    "sheets(properties(sheetId,title),"
    "protectedRanges(protectedRangeId,range,namedRangeId,description,warningOnly,"
    "unprotectedRanges,editors))"
)


class GoogleRetryableTransportError(GoogleAdapterError):
    """Network/quota/server failure safe for the durable worker to retry."""


class GoogleTerminalTransportError(DomainError):
    """Provider request rejected in a way that requires operator remediation."""


class GoogleSheetsTransport(Protocol):
    """Small transport seam used by the live provider and unit tests."""

    @property
    def managed_editor_email(self) -> str:
        """Identity that must retain edit access to managed protected ranges."""
        ...

    def read_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        """Read one bounded values range from a spreadsheet."""
        ...

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

    def read_control_state(self, spreadsheet_id: str) -> Mapping[str, Any]:
        """Read bounded tab/protected-range metadata needed for protection attestation."""
        ...

    def batch_update_spreadsheet(
        self,
        spreadsheet_id: str,
        requests_: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        """Apply structural spreadsheet requests such as protected-range updates."""
        ...


class GoogleSheetsApiTransport:
    """Real Sheets v4 transport authenticated with a service-account file."""

    def __init__(
        self,
        authorized_session: AuthorizedSession,
        *,
        managed_editor_email: str = "service-account",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._session = authorized_session
        self._managed_editor_email = managed_editor_email
        self._timeout_seconds = timeout_seconds

    @property
    def managed_editor_email(self) -> str:
        return self._managed_editor_email

    @classmethod
    def from_service_account_file(cls, credentials_file: str) -> GoogleSheetsApiTransport:
        """Build an authorized transport from an externally mounted key file."""

        try:
            credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                credentials_file,
                scopes=[SHEETS_SCOPE],
            )
        except (ValueError, OSError, GoogleAuthError) as exc:
            raise GoogleTerminalTransportError(
                "Google service-account credentials could not be loaded",
                details={"code": "GOOGLE_CREDENTIALS_INVALID"},
            ) from exc
        editor_email = str(getattr(credentials, "service_account_email", "") or "")
        if not editor_email:
            raise GoogleTerminalTransportError(
                "Google service-account credentials have no client identity",
                details={"code": "GOOGLE_CREDENTIALS_INVALID"},
            )
        return cls(
            AuthorizedSession(credentials),  # type: ignore[no-untyped-call]
            managed_editor_email=editor_email,
        )

    def read_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        url = (
            f"{SHEETS_API_BASE}/{quote(spreadsheet_id, safe='')}/values/"
            f"{quote(range_a1, safe='')}"
            "?valueRenderOption=UNFORMATTED_VALUE&dateTimeRenderOption=SERIAL_NUMBER"
        )
        payload = self._request_json("GET", url)
        values = payload.get("values", [])
        if not isinstance(values, list) or any(not isinstance(row, list) for row in values):
            raise GoogleRetryableTransportError(
                "Google Sheets returned an invalid values payload",
                details={"code": "GOOGLE_LIVE_RESPONSE_INVALID"},
            )
        return [list(row) for row in values]

    def existing_row_count(self, spreadsheet_id: str, range_a1: str) -> int:
        return len(self.read_values(spreadsheet_id, range_a1))

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

    def read_control_state(self, spreadsheet_id: str) -> Mapping[str, Any]:
        url = (
            f"{SHEETS_API_BASE}/{quote(spreadsheet_id, safe='')}"
            f"?fields={quote(_CONTROL_FIELDS, safe='(),')}"
        )
        return self._request_json("GET", url)

    def batch_update_spreadsheet(
        self,
        spreadsheet_id: str,
        requests_: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        url = f"{SHEETS_API_BASE}/{quote(spreadsheet_id, safe='')}:batchUpdate"
        return self._request_json(
            "POST",
            url,
            json_body={"requests": [dict(item) for item in requests_]},
        )

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        json_body: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        try:
            response = self._session.request(  # type: ignore[no-untyped-call]
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
