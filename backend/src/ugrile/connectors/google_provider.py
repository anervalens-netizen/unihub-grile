"""Google projection provider boundary with explicit fake/live implementations."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session

from ..core.config import Settings, get_settings
from ..domain.errors import DomainError
from ..repositories.models import SheetBinding, SheetProjectionRun
from . import google as fake_google
from .google import StoreProjection
from .google_live import (
    GoogleSheetsApiTransport,
    GoogleSheetsTransport,
)


class GoogleProviderConfigurationError(DomainError):
    """Terminal provider configuration/contract failure."""


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
        expected_spreadsheet_id: str | None = None,
        expected_sheet_name_grila: str | None = None,
        expected_sheet_name_pontaj: str | None = None,
    ) -> StoreProjection:
        """Publish one store projection and return the accepted projection."""
        ...


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
        expected_spreadsheet_id: str | None = None,
        expected_sheet_name_grila: str | None = None,
        expected_sheet_name_pontaj: str | None = None,
    ) -> StoreProjection:
        expected = _expected_binding_identity(
            store_id=store_id,
            spreadsheet_id=expected_spreadsheet_id,
            sheet_name_grila=expected_sheet_name_grila,
            sheet_name_pontaj=expected_sheet_name_pontaj,
        )
        binding = fake_google.binding_for(
            session,
            tenant_id=tenant_id,
            store_id=store_id,
        )
        if expected is not None:
            if binding is None:
                deterministic = (
                    fake_google.fake_spreadsheet_id(tenant_id, store_id),
                    "Grila",
                    "Pontaj",
                )
                if expected != deterministic:
                    raise _stale_binding_error(store_id)
            elif _binding_identity(binding) != expected:
                raise _stale_binding_error(store_id)
        return fake_google.write_store_projection(
            session,
            tenant_id=tenant_id,
            store_id=store_id,
            generation=generation,
            payload=payload,
            spreadsheet_id=expected[0] if expected is not None else None,
        )


class LiveGoogleProjectionProvider:
    """Real Google Sheets publisher for an already-bound store workbook."""

    name = "live"

    def __init__(self, transport: GoogleSheetsTransport) -> None:
        self._transport = transport

    def write_store_projection(
        self,
        session: Session,
        *,
        tenant_id: str,
        store_id: str,
        generation: str,
        payload: Mapping[str, Any],
        expected_spreadsheet_id: str | None = None,
        expected_sheet_name_grila: str | None = None,
        expected_sheet_name_pontaj: str | None = None,
    ) -> StoreProjection:
        grila, pontaj = _validate_live_payload(
            tenant_id=tenant_id,
            store_id=store_id,
            generation=generation,
            payload=payload,
        )
        binding = fake_google.binding_for(
            session,
            tenant_id=tenant_id,
            store_id=store_id,
        )
        if binding is None or not binding.spreadsheet_id:
            raise GoogleProviderConfigurationError(
                "live Google projection requires an existing store Sheet binding",
                details={"code": "GOOGLE_SHEET_BINDING_REQUIRED", "store_id": store_id},
            )
        if not binding.sheet_name_grila or not binding.sheet_name_pontaj:
            raise GoogleProviderConfigurationError(
                "store Sheet binding is missing required tab names",
                details={"code": "GOOGLE_SHEET_TAB_BINDING_INVALID", "store_id": store_id},
            )
        expected = _expected_binding_identity(
            store_id=store_id,
            spreadsheet_id=expected_spreadsheet_id,
            sheet_name_grila=expected_sheet_name_grila,
            sheet_name_pontaj=expected_sheet_name_pontaj,
        )
        if expected is not None and _binding_identity(binding) != expected:
            raise _stale_binding_error(store_id)

        grila_values = _grila_values(grila, generation=generation)
        pontaj_values = _pontaj_values(pontaj, generation=generation)
        try:
            grila_existing_rows = self._transport.existing_row_count(
                binding.spreadsheet_id,
                f"{_quote_sheet(binding.sheet_name_grila)}!A:E",
            )
            pontaj_existing_rows = self._transport.existing_row_count(
                binding.spreadsheet_id,
                f"{_quote_sheet(binding.sheet_name_pontaj)}!A:G",
            )
            grila_write = _pad_rows(grila_values, width=5, existing_rows=grila_existing_rows)
            pontaj_write = _pad_rows(pontaj_values, width=7, existing_rows=pontaj_existing_rows)
            self._transport.batch_update_values(
                binding.spreadsheet_id,
                [
                    {
                        "range": (
                            f"{_quote_sheet(binding.sheet_name_grila)}!A1:"
                            f"E{len(grila_write)}"
                        ),
                        "majorDimension": "ROWS",
                        "values": grila_write,
                    },
                    {
                        "range": (
                            f"{_quote_sheet(binding.sheet_name_pontaj)}!A1:"
                            f"G{len(pontaj_write)}"
                        ),
                        "majorDimension": "ROWS",
                        "values": pontaj_write,
                    },
                ],
            )
        except DomainError as exc:
            _record_live_failure(
                session,
                tenant_id=tenant_id,
                store_id=store_id,
                generation=generation,
                payload=payload,
                exc=exc,
            )
            raise

        binding.generation = generation
        binding.updated_at = datetime.now(tz=UTC)
        session.add(
            SheetProjectionRun(
                tenant_id=tenant_id,
                store_id=store_id,
                status="DONE",
                last_error=None,
                last_success_generation=generation,
                last_run_at=datetime.now(tz=UTC),
                payload=_json_dumps({"grila": dict(grila), "pontaj": dict(pontaj)}),
                generation=generation,
                failures=0,
            )
        )
        return StoreProjection(
            store_id=store_id,
            generation=generation,
            grila=dict(grila),
            pontaj=dict(pontaj),
            last_success_generation=generation,
        )


def build_google_projection_provider(
    settings: Settings | None = None,
    *,
    live_transport: GoogleSheetsTransport | None = None,
) -> GoogleProjectionProvider:
    """Resolve the configured projection provider without silent fallback."""

    resolved = settings or get_settings()
    if resolved.google_provider == "fake":
        return FakeGoogleProjectionProvider()

    if not resolved.google_live_mutations_enabled:
        raise GoogleProviderConfigurationError(
            "live Google mutations are disabled",
            details={"code": "GOOGLE_LIVE_MUTATIONS_DISABLED"},
        )

    credentials_file = resolved.google_credentials_file
    if not credentials_file:
        raise GoogleProviderConfigurationError(
            "live Google provider requires an external credentials file",
            details={"code": "GOOGLE_CREDENTIALS_FILE_REQUIRED"},
        )

    credentials_path = Path(credentials_file).expanduser()
    if not credentials_path.is_file():
        raise GoogleProviderConfigurationError(
            "configured Google credentials file is not available",
            details={"code": "GOOGLE_CREDENTIALS_FILE_UNAVAILABLE"},
        )

    transport = live_transport or GoogleSheetsApiTransport.from_service_account_file(
        str(credentials_path)
    )
    return LiveGoogleProjectionProvider(transport)


def _binding_identity(binding: SheetBinding) -> tuple[str, str, str]:
    return (
        binding.spreadsheet_id,
        binding.sheet_name_grila,
        binding.sheet_name_pontaj,
    )


def _expected_binding_identity(
    *,
    store_id: str,
    spreadsheet_id: str | None,
    sheet_name_grila: str | None,
    sheet_name_pontaj: str | None,
) -> tuple[str, str, str] | None:
    values = (spreadsheet_id, sheet_name_grila, sheet_name_pontaj)
    if values == (None, None, None):
        return None
    if any(not isinstance(value, str) or not value for value in values):
        raise GoogleProviderConfigurationError(
            "projection Sheet binding pin is incomplete",
            details={"code": "GOOGLE_SHEET_BINDING_PIN_INVALID", "store_id": store_id},
        )
    return (str(spreadsheet_id), str(sheet_name_grila), str(sheet_name_pontaj))


def _stale_binding_error(store_id: str) -> GoogleProviderConfigurationError:
    return GoogleProviderConfigurationError(
        "projection Sheet binding changed after the job was enqueued",
        details={"code": "GOOGLE_SHEET_BINDING_STALE", "store_id": store_id},
    )


def _validate_live_payload(
    *,
    tenant_id: str,
    store_id: str,
    generation: str,
    payload: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if not store_id:
        raise GoogleProviderConfigurationError(
            "store_id is required",
            details={"code": "GOOGLE_PROJECTION_STORE_REQUIRED", "tenant_id": tenant_id},
        )
    if not generation:
        raise GoogleProviderConfigurationError(
            "generation is required",
            details={"code": "GOOGLE_PROJECTION_GENERATION_REQUIRED", "store_id": store_id},
        )
    grila = payload.get("grila")
    pontaj = payload.get("pontaj")
    if not isinstance(grila, Mapping) or not isinstance(pontaj, Mapping):
        raise GoogleProviderConfigurationError(
            "projection payload requires Grila and Pontaj mappings",
            details={"code": "GOOGLE_PROJECTION_STRUCTURE_INVALID", "store_id": store_id},
        )
    return grila, pontaj


def _grila_values(grila: Mapping[str, Any], *, generation: str) -> list[list[Any]]:
    target = grila.get("target")
    target_mapping = target if isinstance(target, Mapping) else {}
    values: list[list[Any]] = [
        ["UGRILE_PROJECTION", "v1"],
        ["generation", generation],
        ["revision", _cell(grila.get("revision"))],
        ["generated_at", _cell(grila.get("generated_at"))],
        ["target_amount", _cell(target_mapping.get("amount"))],
        ["target_currency", _cell(target_mapping.get("currency"))],
        ["target_version", _cell(target_mapping.get("version"))],
        ["target_sales_days", _cell(target_mapping.get("sales_days"))],
        ["", ""],
        ["business_date", "person_id", "status", "working_kind", "revision"],
    ]
    for row in _projection_rows(grila, "Grila"):
        values.append(
            [
                _cell(row.get("business_date")),
                _cell(row.get("person_id")),
                _cell(row.get("status")),
                _cell(row.get("working_kind")),
                _cell(row.get("revision")),
            ]
        )
    return values


def _pontaj_values(pontaj: Mapping[str, Any], *, generation: str) -> list[list[Any]]:
    values: list[list[Any]] = [
        ["UGRILE_PROJECTION", "v1"],
        ["generation", generation],
        ["revision", _cell(pontaj.get("revision"))],
        ["", ""],
        [
            "person_id",
            "business_date",
            "status",
            "start_time",
            "end_time",
            "pause_minutes",
            "hours",
        ],
    ]
    for row in _projection_rows(pontaj, "Pontaj"):
        values.append(
            [
                _cell(row.get("person_id")),
                _cell(row.get("business_date")),
                _cell(row.get("status")),
                _cell(row.get("start_time")),
                _cell(row.get("end_time")),
                _cell(row.get("pause_minutes")),
                _cell(row.get("hours")),
            ]
        )
    return values


def _projection_rows(block: Mapping[str, Any], label: str) -> Sequence[Mapping[str, Any]]:
    rows = block.get("rows", [])
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise GoogleProviderConfigurationError(
            f"{label} projection rows must be a list of mappings",
            details={"code": "GOOGLE_PROJECTION_ROWS_INVALID", "sheet": label},
        )
    return rows


def _pad_rows(
    rows: Sequence[Sequence[Any]],
    *,
    width: int,
    existing_rows: int,
) -> list[list[Any]]:
    target_rows = max(len(rows), existing_rows, 1)
    result: list[list[Any]] = []
    for row in rows:
        normalized = list(row[:width])
        normalized.extend([""] * (width - len(normalized)))
        result.append(normalized)
    blank = [""] * width
    result.extend([list(blank) for _ in range(target_rows - len(result))])
    return result


def _record_live_failure(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    generation: str,
    payload: Mapping[str, Any],
    exc: DomainError,
) -> None:
    last_good = fake_google.read_store_projection(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
    )
    detail_code = exc.details.get("code")
    code = detail_code if isinstance(detail_code, str) and detail_code else exc.code
    session.add(
        SheetProjectionRun(
            tenant_id=tenant_id,
            store_id=store_id,
            status="FAILED",
            last_error=f"{code}: {exc.message}",
            last_success_generation=(
                last_good.last_success_generation if last_good is not None else None
            ),
            last_run_at=datetime.now(tz=UTC),
            payload=_json_dumps(payload),
            generation=generation,
            failures=1,
        )
    )


def _quote_sheet(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def _cell(value: Any) -> str | int | float | bool:
    if value is None:
        return ""
    if isinstance(value, bool | int | float | str):
        return value
    return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "FakeGoogleProjectionProvider",
    "GoogleProjectionProvider",
    "GoogleProviderConfigurationError",
    "LiveGoogleProjectionProvider",
    "build_google_projection_provider",
]
