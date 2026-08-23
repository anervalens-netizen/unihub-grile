"""GS-003 provider tests proving queued work cannot be redirected by rebind."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from ugrile.connectors.google import write_store_projection
from ugrile.connectors.google_provider import (
    FakeGoogleProjectionProvider,
    GoogleProviderConfigurationError,
    LiveGoogleProjectionProvider,
)
from ugrile.domain.errors import DomainError
from ugrile.repositories.models import SheetBinding, SheetProjectionRun


class RecordingTransport:
    def __init__(self) -> None:
        self.reads: list[tuple[str, str]] = []
        self.readbacks: list[tuple[str, str]] = []
        self.writes: list[tuple[str, Sequence[Mapping[str, Any]]]] = []
        self._written: dict[str, list[list[Any]]] = {}

    def existing_row_count(self, spreadsheet_id: str, range_a1: str) -> int:
        self.reads.append((spreadsheet_id, range_a1))
        return 0

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        self.writes.append((spreadsheet_id, data))
        for item in data:
            key = "Grila" if "Grila" in str(item["range"]) else "Pontaj"
            raw_values = item["values"]
            assert isinstance(raw_values, list)
            self._written[key] = [list(row) for row in raw_values]
        return {"spreadsheetId": spreadsheet_id}

    def read_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        self.readbacks.append((spreadsheet_id, range_a1))
        key = "Grila" if "Grila" in range_a1 else "Pontaj"
        match = re.search(r"(\d+)$", range_a1)
        assert match is not None
        return [list(row) for row in self._written[key][: int(match.group(1))]]


def _payload() -> dict[str, Any]:
    return {
        "grila": {"revision": 1, "rows": []},
        "pontaj": {"revision": 1, "rows": []},
    }


def _binding(session, faker_tenant, *, spreadsheet_id: str = "sheet-original") -> SheetBinding:
    binding = SheetBinding(
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        spreadsheet_id=spreadsheet_id,
        sheet_name_grila="Grila",
        sheet_name_pontaj="Pontaj",
        generation="UNPROJECTED",
    )
    session.add(binding)
    session.flush()
    return binding


def test_live_provider_rejects_rebound_sheet_before_any_provider_io(session, faker_tenant) -> None:
    binding = _binding(session, faker_tenant)
    transport = RecordingTransport()
    provider = LiveGoogleProjectionProvider(transport)

    binding.spreadsheet_id = "sheet-replacement"
    session.flush()

    with pytest.raises(GoogleProviderConfigurationError) as excinfo:
        provider.write_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            generation="LIVE_V1",
            payload=_payload(),
            expected_spreadsheet_id="sheet-original",
            expected_sheet_name_grila="Grila",
            expected_sheet_name_pontaj="Pontaj",
        )

    assert excinfo.value.details["code"] == "GOOGLE_SHEET_BINDING_STALE"
    assert transport.reads == []
    assert transport.readbacks == []
    assert transport.writes == []
    assert session.query(SheetProjectionRun).count() == 0


def test_live_provider_rejects_tab_only_rebind_before_any_provider_io(session, faker_tenant) -> None:
    binding = _binding(session, faker_tenant)
    transport = RecordingTransport()
    provider = LiveGoogleProjectionProvider(transport)

    binding.sheet_name_grila = "Grila v2"
    session.flush()

    with pytest.raises(GoogleProviderConfigurationError) as excinfo:
        provider.write_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            generation="LIVE_V1",
            payload=_payload(),
            expected_spreadsheet_id="sheet-original",
            expected_sheet_name_grila="Grila",
            expected_sheet_name_pontaj="Pontaj",
        )

    assert excinfo.value.details["code"] == "GOOGLE_SHEET_BINDING_STALE"
    assert transport.reads == []
    assert transport.readbacks == []
    assert transport.writes == []


def test_live_provider_accepts_exact_binding_pin(session, faker_tenant) -> None:
    _binding(session, faker_tenant)
    transport = RecordingTransport()
    provider = LiveGoogleProjectionProvider(transport)

    projection = provider.write_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        generation="LIVE_V1",
        payload=_payload(),
        expected_spreadsheet_id="sheet-original",
        expected_sheet_name_grila="Grila",
        expected_sheet_name_pontaj="Pontaj",
    )

    assert projection.generation == "LIVE_V1"
    assert projection.reconciliation["verified"] is True
    assert transport.reads == [
        ("sheet-original", "'Grila'!A:E"),
        ("sheet-original", "'Pontaj'!A:G"),
    ]
    assert transport.readbacks == [
        ("sheet-original", "'Grila'!A1:E15"),
        ("sheet-original", "'Pontaj'!A1:G11"),
    ]
    assert len(transport.writes) == 1
    assert transport.writes[0][0] == "sheet-original"


def test_fake_projection_does_not_hijack_existing_manual_binding(session, faker_tenant) -> None:
    binding = _binding(session, faker_tenant, spreadsheet_id="sheet-manual")

    projection = write_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        generation="FAKE_V1",
        payload=_payload(),
    )
    session.flush()

    assert projection.generation == "FAKE_V1"
    assert binding.spreadsheet_id == "sheet-manual"
    assert binding.generation == "FAKE_V1"


def test_fake_projection_rejects_explicit_identity_change(session, faker_tenant) -> None:
    binding = _binding(session, faker_tenant, spreadsheet_id="sheet-manual")

    with pytest.raises(DomainError) as excinfo:
        write_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            generation="FAKE_V1",
            payload=_payload(),
            spreadsheet_id="sheet-other",
        )

    assert excinfo.value.details["code"] == "SHEET_BINDING_STALE"
    assert binding.spreadsheet_id == "sheet-manual"


def test_fake_provider_rejects_stale_complete_pin(session, faker_tenant) -> None:
    _binding(session, faker_tenant, spreadsheet_id="sheet-manual")
    provider = FakeGoogleProjectionProvider()

    with pytest.raises(GoogleProviderConfigurationError) as excinfo:
        provider.write_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            generation="FAKE_V1",
            payload=_payload(),
            expected_spreadsheet_id="sheet-old",
            expected_sheet_name_grila="Grila",
            expected_sheet_name_pontaj="Pontaj",
        )

    assert excinfo.value.details["code"] == "GOOGLE_SHEET_BINDING_STALE"
