"""GS-003 provider tests proving queued work cannot be redirected by rebind."""

from __future__ import annotations

import copy
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
    managed_editor_email = "svc-binding@example.test"

    def __init__(self) -> None:
        self.reads: list[tuple[str, str]] = []
        self.readbacks: list[tuple[str, str]] = []
        self.writes: list[tuple[str, Sequence[Mapping[str, Any]]]] = []
        self.control_reads: list[str] = []
        self.control_updates: list[tuple[str, Sequence[Mapping[str, Any]]]] = []
        self._written: dict[str, list[list[Any]]] = {}
        self._next_protection_id = 100
        self.control_state: dict[str, Any] = {
            "sheets": [
                {
                    "properties": {"sheetId": 101, "title": "Grila"},
                    "protectedRanges": [],
                },
                {
                    "properties": {"sheetId": 202, "title": "Pontaj"},
                    "protectedRanges": [],
                },
            ]
        }

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
            range_a1 = str(item["range"])
            key = self._value_key(range_a1)
            raw_values = item["values"]
            assert isinstance(raw_values, list)
            self._written[key] = [list(row) for row in raw_values]
        return {"spreadsheetId": spreadsheet_id}

    def read_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        self.readbacks.append((spreadsheet_id, range_a1))
        key = self._value_key(range_a1)
        match = re.search(r"(\d+)$", range_a1)
        limit = int(match.group(1)) if match is not None else 2**31 - 1
        return [list(row) for row in self._written.get(key, [])[:limit]]

    def read_control_state(self, spreadsheet_id: str) -> Mapping[str, Any]:
        self.control_reads.append(spreadsheet_id)
        return copy.deepcopy(self.control_state)

    def batch_update_spreadsheet(
        self,
        spreadsheet_id: str,
        requests_: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        self.control_updates.append((spreadsheet_id, requests_))
        for request in requests_:
            if "addProtectedRange" in request:
                payload = copy.deepcopy(
                    dict(request["addProtectedRange"])["protectedRange"]
                )
                assert isinstance(payload, dict)
                payload["protectedRangeId"] = self._next_protection_id
                self._next_protection_id += 1
                sheet_id = int(dict(payload["range"])["sheetId"])
                self._sheet(sheet_id)["protectedRanges"].append(payload)
            elif "updateProtectedRange" in request:
                update = dict(request["updateProtectedRange"])
                payload = copy.deepcopy(dict(update["protectedRange"]))
                protection_id = int(payload["protectedRangeId"])
                sheet_id = int(dict(payload["range"])["sheetId"])
                sheet = self._sheet(sheet_id)
                sheet["protectedRanges"] = [
                    payload if item.get("protectedRangeId") == protection_id else item
                    for item in sheet["protectedRanges"]
                ]
            elif "deleteProtectedRange" in request:
                protection_id = int(
                    dict(request["deleteProtectedRange"])["protectedRangeId"]
                )
                for sheet in self.control_state["sheets"]:
                    sheet["protectedRanges"] = [
                        item
                        for item in sheet["protectedRanges"]
                        if item.get("protectedRangeId") != protection_id
                    ]
            else:
                raise AssertionError(f"unexpected control request: {request}")
        return {"spreadsheetId": spreadsheet_id}

    def _value_key(self, range_a1: str) -> str:
        if "Grila" in range_a1 and "!G" in range_a1:
            return "Epay"
        return "Grila" if "Grila" in range_a1 else "Pontaj"

    def _sheet(self, sheet_id: int) -> dict[str, Any]:
        for sheet in self.control_state["sheets"]:
            if sheet["properties"]["sheetId"] == sheet_id:
                return sheet
        raise AssertionError(f"unknown sheet id {sheet_id}")


def _payload() -> dict[str, Any]:
    return {
        "metadata": {
            "month_id": "month_binding_pin",
            "revision": 1,
            "store_id": "store_test",
            "year": 2026,
            "month": 8,
            "rule_pack_version": "rule-pack-test-v1",
            "projected_at": "2026-08-23T12:00:00+00:00",
        },
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
    assert transport.control_reads == []
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
    assert transport.control_reads == []


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
        ("sheet-original", "'Grila'!G1:I260"),
        ("sheet-original", "'Grila'!A1:E15"),
        ("sheet-original", "'Pontaj'!A1:G11"),
        ("sheet-original", "'Grila'!G1:I4"),
    ]
    assert transport.control_reads == ["sheet-original", "sheet-original"]
    assert len(transport.control_updates) == 1
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
