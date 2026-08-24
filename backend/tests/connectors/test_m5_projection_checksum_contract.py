"""GS-006 exact checksum parity between canonical format and live readback."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from ugrile.connectors.google_projection_format import reconciliation_metadata
from ugrile.connectors.google_provider import LiveGoogleProjectionProvider
from ugrile.repositories.models import SheetBinding


class EchoTransport:
    managed_editor_email = "svc-checksum@example.test"

    def spreadsheet_owner_emails(self, spreadsheet_id: str) -> frozenset[str]:
        return frozenset()

    def __init__(self) -> None:
        self.written: dict[str, list[list[Any]]] = {}
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
        return 0

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        for item in data:
            range_a1 = str(item["range"])
            key = self._value_key(range_a1)
            values = item["values"]
            assert isinstance(values, list)
            self.written[key] = [list(row) for row in values]
        return {"spreadsheetId": spreadsheet_id}

    def read_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        key = self._value_key(range_a1)
        return [list(row) for row in self.written.get(key, [])]

    def read_control_state(self, spreadsheet_id: str) -> Mapping[str, Any]:
        return copy.deepcopy(self.control_state)

    def batch_update_spreadsheet(
        self,
        spreadsheet_id: str,
        requests_: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
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


def _payload(store_id: str) -> dict[str, Any]:
    return {
        "metadata": {
            "store_id": store_id,
            "month_id": "month_checksum",
            "year": 2026,
            "month": 8,
            "revision": 9,
            "rule_pack_version": "rule-pack-checksum-v1",
            "projected_at": "2026-08-23T12:34:56+00:00",
        },
        "grila": {
            "revision": 9,
            "generated_at": "2026-08-23T12:34:56+00:00",
            "target": {
                "amount": "12345.67",
                "currency": "RON",
                "version": 3,
                "sales_days": 30,
            },
            "rows": [
                {
                    "business_date": "2026-08-03",
                    "person_id": "person_a",
                    "status": "WORKING",
                    "working_kind": "NORMAL",
                    "revision": 9,
                }
            ],
        },
        "pontaj": {
            "revision": 9,
            "rows": [
                {
                    "person_id": "person_a",
                    "business_date": "2026-08-03",
                    "status": "WORKING",
                    "start_time": "09:00:00",
                    "end_time": "18:00:00",
                    "pause_minutes": 60,
                    "hours": "8.00",
                }
            ],
        },
    }


def test_live_readback_checksum_matches_canonical_projection_exactly(
    session,
    faker_tenant,
) -> None:
    store_id = faker_tenant["store_id"]
    session.add(
        SheetBinding(
            tenant_id=faker_tenant["tenant_id"],
            store_id=store_id,
            spreadsheet_id="sheet-checksum",
            sheet_name_grila="Grila",
            sheet_name_pontaj="Pontaj",
            generation="UNPROJECTED",
        )
    )
    session.flush()
    payload = _payload(store_id)
    provider = LiveGoogleProjectionProvider(EchoTransport())

    projection = provider.write_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=store_id,
        generation="LIVE_CHECKSUM_V2",
        payload=payload,
    )
    expected = reconciliation_metadata(
        payload,
        generation="LIVE_CHECKSUM_V2",
        verification_mode="live_readback",
        verified=True,
    )

    assert projection.reconciliation == expected
    assert len(str(projection.reconciliation["grila_checksum_sha256"])) == 64
    assert len(str(projection.reconciliation["pontaj_checksum_sha256"])) == 64
    assert len(str(projection.reconciliation["projection_checksum_sha256"])) == 64
