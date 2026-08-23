"""M5 live Google provider tests with no external network access."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from ugrile.connectors import google_live
from ugrile.connectors.google import (
    last_error,
    read_store_projection,
    write_store_projection,
)
from ugrile.connectors.google_live import (
    SHEETS_SCOPE,
    GoogleRetryableTransportError,
    GoogleSheetsApiTransport,
    GoogleTerminalTransportError,
)
from ugrile.connectors.google_provider import (
    GoogleProviderConfigurationError,
    LiveGoogleProjectionProvider,
)
from ugrile.repositories.models import SheetBinding, SheetProjectionRun


class RecordingTransport:
    def __init__(self, *, grila_rows: int = 0, pontaj_rows: int = 0) -> None:
        self.grila_rows = grila_rows
        self.pontaj_rows = pontaj_rows
        self.count_reads: list[tuple[str, str]] = []
        self.readbacks: list[tuple[str, str]] = []
        self.writes: list[tuple[str, Sequence[Mapping[str, Any]]]] = []
        self._written: dict[str, list[list[Any]]] = {}

    def existing_row_count(self, spreadsheet_id: str, range_a1: str) -> int:
        self.count_reads.append((spreadsheet_id, range_a1))
        return self.grila_rows if "Grila" in range_a1 else self.pontaj_rows

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        self.writes.append((spreadsheet_id, data))
        for item in data:
            range_a1 = str(item["range"])
            key = "Grila" if "Grila" in range_a1 else "Pontaj"
            raw_values = item["values"]
            assert isinstance(raw_values, list)
            self._written[key] = [list(row) for row in raw_values]
        return {"spreadsheetId": spreadsheet_id, "totalUpdatedCells": 42}

    def read_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        self.readbacks.append((spreadsheet_id, range_a1))
        key = "Grila" if "Grila" in range_a1 else "Pontaj"
        match = re.search(r"(\d+)$", range_a1)
        assert match is not None
        return [list(row) for row in self._written[key][: int(match.group(1))]]


class OmittingTrailingBlankTransport(RecordingTransport):
    """Match Google Values GET, which may omit all-blank trailing rows."""

    def read_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        rows = super().read_values(spreadsheet_id, range_a1)
        while rows and all(value == "" for value in rows[-1]):
            rows.pop()
        return rows


class FailingTransport(RecordingTransport):
    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        raise GoogleRetryableTransportError(
            "simulated provider timeout",
            details={"code": "GOOGLE_LIVE_TRANSPORT_ERROR"},
        )


class MismatchTransport(RecordingTransport):
    def read_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        rows = super().read_values(spreadsheet_id, range_a1)
        if "Grila" in range_a1:
            rows[-1][0] = "tampered-date"
        return rows


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeAuthorizedSession:
    def __init__(self, responses: Sequence[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self._responses:
            raise AssertionError("unexpected request")
        return self._responses.pop(0)


def _binding(session, faker_tenant, *, generation: str = "BINDING_V1") -> SheetBinding:
    binding = SheetBinding(
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        spreadsheet_id="sheet-live-123",
        sheet_name_grila="Grila",
        sheet_name_pontaj="Pontaj",
        generation=generation,
    )
    session.add(binding)
    session.flush()
    return binding


def _payload() -> dict[str, Any]:
    return {
        "metadata": {
            "store_id": "store_test",
            "month_id": "month_test",
            "year": 2026,
            "month": 8,
            "revision": 7,
            "rule_pack_version": "rule-pack-test-v1",
            "projected_at": "2026-08-23T12:00:00+00:00",
        },
        "grila": {
            "revision": 7,
            "generated_at": "2026-08-23T12:00:00+00:00",
            "target": {
                "amount": "10000.00",
                "currency": "RON",
                "version": 2,
                "sales_days": 31,
            },
            "rows": [
                {
                    "business_date": "2026-08-01",
                    "person_id": "person_a",
                    "status": "WORKING",
                    "working_kind": "NORMAL",
                    "revision": 7,
                }
            ],
        },
        "pontaj": {
            "revision": 7,
            "rows": [
                {
                    "person_id": "person_a",
                    "business_date": "2026-08-01",
                    "status": "WORKING",
                    "start_time": "09:00:00",
                    "end_time": "18:00:00",
                    "pause_minutes": 60,
                    "hours": "8.00",
                }
            ],
        },
    }


def test_live_provider_requires_existing_binding(session, faker_tenant) -> None:
    provider = LiveGoogleProjectionProvider(RecordingTransport())

    with pytest.raises(GoogleProviderConfigurationError) as excinfo:
        provider.write_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            generation="LIVE_V1",
            payload=_payload(),
        )

    assert excinfo.value.details["code"] == "GOOGLE_SHEET_BINDING_REQUIRED"


def test_live_provider_writes_reads_back_and_persists_verified_reconciliation(
    session,
    faker_tenant,
) -> None:
    binding = _binding(session, faker_tenant)
    transport = RecordingTransport(grila_rows=20, pontaj_rows=15)
    provider = LiveGoogleProjectionProvider(transport)

    projection = provider.write_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        generation="LIVE_V2",
        payload=_payload(),
    )
    session.commit()

    assert projection.generation == "LIVE_V2"
    assert transport.count_reads == [
        ("sheet-live-123", "'Grila'!A:E"),
        ("sheet-live-123", "'Pontaj'!A:G"),
    ]
    assert transport.readbacks == [
        ("sheet-live-123", "'Grila'!A1:E20"),
        ("sheet-live-123", "'Pontaj'!A1:G15"),
    ]
    assert len(transport.writes) == 1
    spreadsheet_id, writes = transport.writes[0]
    assert spreadsheet_id == "sheet-live-123"
    assert writes[0]["range"] == "'Grila'!A1:E20"
    assert writes[1]["range"] == "'Pontaj'!A1:G15"
    assert writes[0]["values"][0] == ["UGRILE_PROJECTION", "v2", "", "", ""]
    assert writes[1]["values"][0] == [
        "UGRILE_PROJECTION",
        "v2",
        "",
        "",
        "",
        "",
        "",
    ]
    assert writes[0]["values"][2][:2] == ["store_id", "store_test"]
    assert writes[0]["values"][7][:2] == ["rule_pack_version", "rule-pack-test-v1"]
    assert writes[0]["values"][-1] == ["", "", "", "", ""]
    assert writes[1]["values"][-1] == ["", "", "", "", "", "", ""]
    assert binding.spreadsheet_id == "sheet-live-123"
    assert binding.generation == "LIVE_V2"
    assert projection.reconciliation["verified"] is True
    assert projection.reconciliation["verification_mode"] == "live_readback"
    assert projection.reconciliation["format_version"] == "v2"
    assert projection.reconciliation["revision"] == 7
    assert projection.reconciliation["rule_pack_version"] == "rule-pack-test-v1"
    assert len(str(projection.reconciliation["projection_checksum_sha256"])) == 64

    persisted = read_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id="month_test",
    )
    assert persisted is not None
    assert persisted.generation == "LIVE_V2"
    assert persisted.grila["revision"] == 7
    assert persisted.reconciliation == projection.reconciliation


def test_live_readback_accepts_google_omitted_blank_tail_after_stale_row_clear(
    session,
    faker_tenant,
) -> None:
    binding = _binding(session, faker_tenant)
    transport = OmittingTrailingBlankTransport(grila_rows=20, pontaj_rows=15)
    provider = LiveGoogleProjectionProvider(transport)

    projection = provider.write_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        generation="LIVE_V2",
        payload=_payload(),
    )
    session.commit()

    assert binding.generation == "LIVE_V2"
    assert projection.reconciliation["verified"] is True
    assert transport.readbacks == [
        ("sheet-live-123", "'Grila'!A1:E20"),
        ("sheet-live-123", "'Pontaj'!A1:G15"),
    ]


def test_live_readback_mismatch_is_retryable_and_does_not_advance_last_good(
    session,
    faker_tenant,
) -> None:
    write_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        generation="GOOD_V1",
        payload=_payload(),
        spreadsheet_id="sheet-live-123",
    )
    session.commit()
    binding = session.query(SheetBinding).filter_by(
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
    ).one()
    assert binding.generation == "GOOD_V1"

    provider = LiveGoogleProjectionProvider(MismatchTransport())
    with pytest.raises(GoogleRetryableTransportError) as excinfo:
        provider.write_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            generation="BAD_V2",
            payload=_payload(),
        )
    session.commit()

    assert excinfo.value.details == {
        "code": "GOOGLE_LIVE_READBACK_MISMATCH",
        "sheet": "Grila",
    }
    session.refresh(binding)
    assert binding.generation == "GOOD_V1"
    persisted = read_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id="month_test",
    )
    assert persisted is not None
    assert persisted.generation == "GOOD_V1"
    assert session.query(SheetProjectionRun).filter_by(
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        status="DONE",
        generation="BAD_V2",
    ).count() == 0
    failed = session.query(SheetProjectionRun).filter_by(
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        status="FAILED",
        generation="BAD_V2",
    ).one()
    assert "GOOGLE_LIVE_READBACK_MISMATCH" in (failed.last_error or "")


def test_month_scoped_readback_does_not_return_another_month(session, faker_tenant) -> None:
    payload = _payload()
    payload["metadata"] = {**payload["metadata"], "month_id": "month_aug"}
    write_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        generation="AUG_V1",
        payload=payload,
    )
    session.commit()

    assert read_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id="month_aug",
    ) is not None
    assert read_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id="month_sep",
    ) is None


def test_live_transport_failure_preserves_last_good_and_records_diagnostic(
    session,
    faker_tenant,
) -> None:
    write_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        generation="GOOD_V1",
        payload=_payload(),
        spreadsheet_id="sheet-live-123",
    )
    session.commit()
    provider = LiveGoogleProjectionProvider(FailingTransport())

    with pytest.raises(GoogleRetryableTransportError):
        provider.write_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            generation="FAILED_V2",
            payload=_payload(),
        )
    session.commit()

    persisted = read_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id="month_test",
    )
    assert persisted is not None
    assert persisted.generation == "GOOD_V1"
    error = last_error(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id="month_test",
    )
    assert error == "GOOGLE_LIVE_TRANSPORT_ERROR: simulated provider timeout"


@pytest.mark.parametrize("status_code", [408, 429, 500, 503])
def test_http_transport_classifies_retryable_responses(status_code: int) -> None:
    session = FakeAuthorizedSession([FakeResponse(status_code, {})])
    transport = GoogleSheetsApiTransport(session)

    with pytest.raises(GoogleRetryableTransportError) as excinfo:
        transport.batch_update_values("sheet-secret", [])

    assert excinfo.value.details == {
        "code": "GOOGLE_LIVE_HTTP_ERROR",
        "status_code": status_code,
    }
    assert "sheet-secret" not in str(excinfo.value)


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
def test_http_transport_classifies_terminal_responses(status_code: int) -> None:
    session = FakeAuthorizedSession([FakeResponse(status_code, {})])
    transport = GoogleSheetsApiTransport(session)

    with pytest.raises(GoogleTerminalTransportError) as excinfo:
        transport.batch_update_values("sheet-secret", [])

    assert excinfo.value.details == {
        "code": "GOOGLE_LIVE_HTTP_ERROR",
        "status_code": status_code,
    }
    assert "sheet-secret" not in str(excinfo.value)


def test_http_transport_reads_unformatted_values_and_uses_batch_update_without_network() -> None:
    session = FakeAuthorizedSession(
        [
            FakeResponse(200, {"values": [["x"], ["y"]]}),
            FakeResponse(200, {"spreadsheetId": "sheet-1", "totalUpdatedCells": 2}),
        ]
    )
    transport = GoogleSheetsApiTransport(session, timeout_seconds=12.5)

    assert transport.read_values("sheet-1", "'Grila'!A:E") == [["x"], ["y"]]
    result = transport.batch_update_values(
        "sheet-1",
        [{"range": "'Grila'!A1:A1", "values": [["ok"]]}],
    )

    assert result["totalUpdatedCells"] == 2
    assert session.calls[0]["method"] == "GET"
    first_url = str(session.calls[0]["url"])
    assert "%27Grila%27%21A%3AE" in first_url
    assert "valueRenderOption=UNFORMATTED_VALUE" in first_url
    assert "dateTimeRenderOption=SERIAL_NUMBER" in first_url
    assert session.calls[1]["method"] == "POST"
    assert str(session.calls[1]["url"]).endswith("/sheet-1/values:batchUpdate")
    assert session.calls[1]["json"] == {
        "valueInputOption": "RAW",
        "data": [{"range": "'Grila'!A1:A1", "values": [["ok"]]}],
    }
    assert session.calls[1]["timeout"] == 12.5


def test_http_transport_rejects_invalid_values_shape() -> None:
    session = FakeAuthorizedSession([FakeResponse(200, {"values": ["not-a-row"]})])
    transport = GoogleSheetsApiTransport(session)

    with pytest.raises(GoogleRetryableTransportError) as excinfo:
        transport.read_values("sheet-1", "'Grila'!A:E")

    assert excinfo.value.details == {"code": "GOOGLE_LIVE_RESPONSE_INVALID"}


def test_service_account_loader_uses_sheet_scope_without_real_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel_credentials = object()
    sentinel_session = FakeAuthorizedSession([])
    calls: dict[str, object] = {}

    def fake_load(filename: str, *, scopes: Sequence[str]) -> object:
        calls["filename"] = filename
        calls["scopes"] = list(scopes)
        return sentinel_credentials

    monkeypatch.setattr(
        google_live.service_account.Credentials,
        "from_service_account_file",
        staticmethod(fake_load),
    )
    monkeypatch.setattr(
        google_live,
        "AuthorizedSession",
        lambda credentials: sentinel_session,
    )

    transport = GoogleSheetsApiTransport.from_service_account_file("/run/secrets/google.json")

    assert calls == {
        "filename": "/run/secrets/google.json",
        "scopes": [SHEETS_SCOPE],
    }
    assert transport._session is sentinel_session
