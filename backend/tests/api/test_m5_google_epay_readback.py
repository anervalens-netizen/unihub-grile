"""GS-007 API proofs for real-Sheet E-pay readback without network I/O."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ugrile.api import google_epay as google_epay_api
from ugrile.connectors.google_epay_layout import render_epay_values
from ugrile.core import database
from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.repositories.models import SheetBinding, SiteDayAssignment
from ugrile.repositories.months import MonthRepository

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}
MANAGER = {"X-Ugrile-Identity": "user_manager", "X-Ugrile-Tenant": "tenant_acme"}


class Reader:
    def __init__(self, rows: list[list[Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, str]] = []

    def read_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        self.calls.append((spreadsheet_id, range_a1))
        return [list(row) for row in self.rows]


def _seed(faker_tenant) -> tuple[str, str]:
    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(
            faker_tenant["tenant_id"],
            2026,
            8,
        )
        session.add(
            SiteDayAssignment(
                tenant_id=faker_tenant["tenant_id"],
                month_id=month.id,
                store_id=faker_tenant["store_id"],
                person_id=faker_tenant["person_a_id"],
                business_date=datetime(2026, 8, 1, tzinfo=UTC).date(),
                status=DayStatus.WORKING.value,
                working_kind=WorkingKind.NORMAL.value,
                revision=0,
            )
        )
        session.add(
            SheetBinding(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["store_id"],
                spreadsheet_id="sheet-live-epay",
                sheet_name_grila="Grila",
                sheet_name_pontaj="Pontaj",
                generation="LIVE_V1",
            )
        )
        session.flush()
        return month.id, faker_tenant["person_a_id"]


def _rows(month_id: str, person_id: str, *, revision: int = 0) -> list[list[Any]]:
    return render_epay_values(
        month_id=month_id,
        revision=revision,
        person_ids=[person_id],
        preserved={person_id: (3, "2")},
    )


def test_google_epay_readback_persists_exact_two_cells_and_becomes_fresh(
    client,
    faker_tenant,
    monkeypatch,
) -> None:
    month_id, person_id = _seed(faker_tenant)
    reader = Reader(_rows(month_id, person_id))
    monkeypatch.setattr(
        google_epay_api,
        "build_google_live_transport",
        lambda *, require_mutations: reader,
    )

    response = client.post(
        f"/months/{month_id}/epay/google-readback",
        params={"store_id": faker_tenant["store_id"]},
        headers=ADMIN,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["structure_valid"] is True
    assert body["structural_errors"] == []
    assert body["valid_count"] == 2
    assert body["invalid_count"] == 0
    assert {(item["category"], item["value"]) for item in body["items"]} == {
        ("UNDER_50", 3),
        ("AT_OR_OVER_50", 2),
    }
    assert reader.calls == [("sheet-live-epay", "'Grila'!G1:I260")]

    freshness = client.get(
        f"/months/{month_id}/epay/freshness",
        params={"store_id": faker_tenant["store_id"]},
        headers=ADMIN,
    )
    assert freshness.status_code == 200, freshness.text
    assert freshness.json()["is_fresh"] is True
    assert freshness.json()["fresh_count"] == 2
    assert freshness.json()["expected_count"] == 2


def test_malformed_sheet_layout_records_latest_invalid_and_blocks_old_fresh_data(
    client,
    faker_tenant,
    monkeypatch,
) -> None:
    month_id, person_id = _seed(faker_tenant)
    reader = Reader(_rows(month_id, person_id))
    monkeypatch.setattr(
        google_epay_api,
        "build_google_live_transport",
        lambda *, require_mutations: reader,
    )

    first = client.post(
        f"/months/{month_id}/epay/google-readback",
        params={"store_id": faker_tenant["store_id"]},
        headers=ADMIN,
    )
    assert first.status_code == 200, first.text
    assert first.json()["valid_count"] == 2

    reader.rows = _rows(month_id, person_id, revision=99)
    second = client.post(
        f"/months/{month_id}/epay/google-readback",
        params={"store_id": faker_tenant["store_id"]},
        headers=ADMIN,
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["structure_valid"] is False
    assert "EPAY_LAYOUT_REVISION_MISMATCH" in body["structural_errors"]
    assert body["valid_count"] == 0
    assert body["invalid_count"] == 2
    assert all(item["is_valid"] is False for item in body["items"])

    freshness = client.get(
        f"/months/{month_id}/epay/freshness",
        params={"store_id": faker_tenant["store_id"]},
        headers=ADMIN,
    )
    assert freshness.status_code == 200, freshness.text
    assert freshness.json()["is_fresh"] is False
    assert freshness.json()["fresh_count"] == 0


def test_google_epay_readback_requires_epay_write_before_provider_io(
    client,
    faker_tenant,
    faker_second_tenant,
    monkeypatch,
) -> None:
    month_id, person_id = _seed(faker_tenant)
    reader = Reader(_rows(month_id, person_id))
    monkeypatch.setattr(
        google_epay_api,
        "build_google_live_transport",
        lambda *, require_mutations: reader,
    )

    manager = client.post(
        f"/months/{month_id}/epay/google-readback",
        params={"store_id": faker_tenant["store_id"]},
        headers=MANAGER,
    )
    outside_store = client.post(
        f"/months/{month_id}/epay/google-readback",
        params={"store_id": faker_second_tenant["store_id"]},
        headers=ADMIN,
    )

    assert manager.status_code == 403, manager.text
    assert outside_store.status_code == 403, outside_store.text
    assert reader.calls == []


def test_known_closed_month_rejects_before_google_read(
    client,
    faker_tenant,
    monkeypatch,
) -> None:
    month_id, person_id = _seed(faker_tenant)
    with database.session_scope() as session:
        month = MonthRepository(session).get(month_id)
        month.state = MonthState.CLOSED.value
    reader = Reader(_rows(month_id, person_id))
    monkeypatch.setattr(
        google_epay_api,
        "build_google_live_transport",
        lambda *, require_mutations: reader,
    )

    response = client.post(
        f"/months/{month_id}/epay/google-readback",
        params={"store_id": faker_tenant["store_id"]},
        headers=ADMIN,
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "CONFLICT"
    assert response.json()["details"]["code"] == "MONTH_CLOSED"
    assert reader.calls == []
