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
from .google_epay_layout import (
    epay_read_range,
    epay_write_range,
    parse_epay_readback,
    preserved_epay_values,
    render_epay_values,
)
from .google_live import (
    GoogleRetryableTransportError,
    GoogleSheetsApiTransport,
    GoogleSheetsTransport,
)
from .google_projection_format import (
    matrices_match,
    reconciliation_metadata,
    render_grila_values,
    render_pontaj_values,
)
from .google_sheet_protection import attest_protection_state, build_protection_requests


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
    """Real Google Sheets publisher with mandatory readback + protection proof."""

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

        grila_values = render_grila_values(payload, generation=generation)
        pontaj_values = render_pontaj_values(payload, generation=generation)
        person_ids = _projection_person_ids(grila)
        metadata = payload.get("metadata")
        month_id = _required_metadata_text(metadata, "month_id")
        revision = _required_metadata_int(metadata, "revision")
        try:
            epay_existing = self._transport.read_values(
                binding.spreadsheet_id,
                epay_read_range(binding.sheet_name_grila),
            )
            preserved = preserved_epay_values(epay_existing, month_id=month_id)
            epay_values = render_epay_values(
                month_id=month_id,
                revision=revision,
                person_ids=person_ids,
                preserved=preserved,
            )
            control_state = self._transport.read_control_state(binding.spreadsheet_id)
            protection_requests = build_protection_requests(
                control_state,
                grila_tab=binding.sheet_name_grila,
                pontaj_tab=binding.sheet_name_pontaj,
                person_count=len(person_ids),
                editor_email=self._transport.managed_editor_email,
            )
            if protection_requests:
                self._transport.batch_update_spreadsheet(
                    binding.spreadsheet_id,
                    protection_requests,
                )
            attested_state = self._transport.read_control_state(binding.spreadsheet_id)
            attest_protection_state(
                attested_state,
                grila_tab=binding.sheet_name_grila,
                pontaj_tab=binding.sheet_name_pontaj,
                person_count=len(person_ids),
                editor_email=self._transport.managed_editor_email,
            )

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
            epay_write = _pad_rows(
                epay_values,
                width=3,
                existing_rows=max(len(epay_existing), 4),
            )
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
                    {
                        "range": (
                            f"{_quote_sheet(binding.sheet_name_grila)}!G1:"
                            f"I{len(epay_write)}"
                        ),
                        "majorDimension": "ROWS",
                        "values": epay_write,
                    },
                ],
            )
            grila_readback = self._transport.read_values(
                binding.spreadsheet_id,
                (
                    f"{_quote_sheet(binding.sheet_name_grila)}!A1:"
                    f"E{len(grila_write)}"
                ),
            )
            pontaj_readback = self._transport.read_values(
                binding.spreadsheet_id,
                (
                    f"{_quote_sheet(binding.sheet_name_pontaj)}!A1:"
                    f"G{len(pontaj_write)}"
                ),
            )
            epay_readback = self._transport.read_values(
                binding.spreadsheet_id,
                epay_write_range(binding.sheet_name_grila, len(epay_write) - 4),
            )
            _require_reconciled_matrix(
                sheet="Grila",
                expected=grila_write,
                actual=grila_readback,
                width=5,
            )
            _require_reconciled_matrix(
                sheet="Pontaj",
                expected=pontaj_write,
                actual=pontaj_readback,
                width=7,
            )
            epay_structure = parse_epay_readback(
                epay_readback,
                month_id=month_id,
                revision=revision,
                expected_person_ids=person_ids,
            )
            if not epay_structure.structure_valid:
                raise GoogleRetryableTransportError(
                    "Google E-pay input block readback did not match its managed identity",
                    details={
                        "code": "GOOGLE_EPAY_LAYOUT_MISMATCH",
                        "errors": list(epay_structure.structural_errors),
                    },
                )
            reconciliation = reconciliation_metadata(
                payload,
                generation=generation,
                verification_mode="live_readback",
                verified=True,
                grila_values=grila_readback[: len(grila_values)],
                pontaj_values=pontaj_readback[: len(pontaj_values)],
            )
        except (DomainError, ValueError) as exc:
            error = _normalize_live_error(exc)
            _record_live_failure(
                session,
                tenant_id=tenant_id,
                store_id=store_id,
                generation=generation,
                payload=payload,
                exc=error,
            )
            raise error from exc if error is not exc else None

        binding.generation = generation
        binding.updated_at = datetime.now(tz=UTC)
        persisted_payload = {
            "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
            "grila": dict(grila),
            "pontaj": dict(pontaj),
            "reconciliation": reconciliation,
        }
        session.add(
            SheetProjectionRun(
                tenant_id=tenant_id,
                store_id=store_id,
                status="DONE",
                last_error=None,
                last_success_generation=generation,
                last_run_at=datetime.now(tz=UTC),
                payload=_json_dumps(persisted_payload),
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
            reconciliation=reconciliation,
        )


def build_google_live_transport(
    settings: Settings | None = None,
    *,
    require_mutations: bool = False,
) -> GoogleSheetsTransport:
    """Build live transport behind explicit provider/credential gates."""

    resolved = settings or get_settings()
    if resolved.google_provider != "live":
        raise GoogleProviderConfigurationError(
            "live Google provider is not selected",
            details={"code": "GOOGLE_LIVE_PROVIDER_REQUIRED"},
        )
    if require_mutations and not resolved.google_live_mutations_enabled:
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
    return GoogleSheetsApiTransport.from_service_account_file(str(credentials_path))


def build_google_projection_provider(
    settings: Settings | None = None,
    *,
    live_transport: GoogleSheetsTransport | None = None,
) -> GoogleProjectionProvider:
    """Resolve the configured projection provider without silent fallback."""

    resolved = settings or get_settings()
    if resolved.google_provider == "fake":
        return FakeGoogleProjectionProvider()
    transport = live_transport or build_google_live_transport(
        resolved,
        require_mutations=True,
    )
    if not resolved.google_live_mutations_enabled:
        raise GoogleProviderConfigurationError(
            "live Google mutations are disabled",
            details={"code": "GOOGLE_LIVE_MUTATIONS_DISABLED"},
        )
    return LiveGoogleProjectionProvider(transport)


def _projection_person_ids(grila: Mapping[str, Any]) -> list[str]:
    rows = grila.get("rows")
    if not isinstance(rows, list):
        return []
    person_ids = {
        str(row.get("person_id"))
        for row in rows
        if isinstance(row, Mapping)
        and row.get("status") == "WORKING"
        and isinstance(row.get("person_id"), str)
        and row.get("person_id")
    }
    return sorted(person_ids)


def _required_metadata_text(metadata: Any, key: str) -> str:
    value = metadata.get(key) if isinstance(metadata, Mapping) else None
    if not isinstance(value, str) or not value:
        raise GoogleProviderConfigurationError(
            "projection metadata is incomplete",
            details={"code": "GOOGLE_PROJECTION_METADATA_INVALID", "field": key},
        )
    return value


def _required_metadata_int(metadata: Any, key: str) -> int:
    value = metadata.get(key) if isinstance(metadata, Mapping) else None
    if type(value) is not int:
        raise GoogleProviderConfigurationError(
            "projection metadata is incomplete",
            details={"code": "GOOGLE_PROJECTION_METADATA_INVALID", "field": key},
        )
    return value


def _normalize_live_error(exc: DomainError | ValueError) -> DomainError:
    if isinstance(exc, DomainError):
        return exc
    return GoogleProviderConfigurationError(
        "Google Sheet E-pay layout exceeds its bounded contract",
        details={"code": "GOOGLE_EPAY_LAYOUT_LIMIT"},
    )


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


def _require_reconciled_matrix(
    *,
    sheet: str,
    expected: Sequence[Sequence[Any]],
    actual: Sequence[Sequence[Any]],
    width: int,
) -> None:
    if matrices_match(expected, actual, width=width):
        return
    raise GoogleRetryableTransportError(
        "Google Sheet readback did not match the projection that was written",
        details={"code": "GOOGLE_LIVE_READBACK_MISMATCH", "sheet": sheet},
    )


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
    metadata = payload.get("metadata")
    month_id = (
        str(metadata.get("month_id"))
        if isinstance(metadata, Mapping) and metadata.get("month_id")
        else None
    )
    last_good = fake_google.read_store_projection(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        month_id=month_id,
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


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "FakeGoogleProjectionProvider",
    "GoogleProjectionProvider",
    "GoogleProviderConfigurationError",
    "LiveGoogleProjectionProvider",
    "build_google_live_transport",
    "build_google_projection_provider",
]
