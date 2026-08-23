"""M5 GS-003 tests for explicit permanent store↔Sheet binding lifecycle."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.exc import IntegrityError

from ugrile.core import database
from ugrile.repositories.models import AuditEvent, SheetBinding

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}
MANAGER = {"X-Ugrile-Identity": "user_manager", "X-Ugrile-Tenant": "tenant_acme"}


def _body(
    spreadsheet_id: str,
    *,
    grila: str = "Grila",
    pontaj: str = "Pontaj",
    expected: str | None = None,
    reason: str | None = None,
) -> dict[str, str]:
    body = {
        "spreadsheet_id": spreadsheet_id,
        "sheet_name_grila": grila,
        "sheet_name_pontaj": pontaj,
    }
    if expected is not None:
        body["expected_current_spreadsheet_id"] = expected
    if reason is not None:
        body["reason"] = reason
    return body


def test_sheet_binding_routes_are_admin_only(client, faker_tenant) -> None:
    store_id = faker_tenant["store_id"]

    get_response = client.get(f"/sheet-bindings/{store_id}", headers=MANAGER)
    put_response = client.put(
        f"/sheet-bindings/{store_id}",
        headers=MANAGER,
        json=_body("sheet-manager-denied"),
    )

    assert get_response.status_code == 403, get_response.text
    assert put_response.status_code == 403, put_response.text
    assert get_response.json()["details"]["capability"] == "sheet.bind"
    assert put_response.json()["details"]["capability"] == "sheet.bind"


def test_create_get_and_exact_replay_are_idempotent(client, faker_tenant) -> None:
    store_id = faker_tenant["store_id"]
    request = _body("sheet-primary")

    first = client.put(f"/sheet-bindings/{store_id}", headers=ADMIN, json=request)
    replay = client.put(f"/sheet-bindings/{store_id}", headers=ADMIN, json=request)
    readback = client.get(f"/sheet-bindings/{store_id}", headers=ADMIN)

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert readback.status_code == 200, readback.text
    assert first.json() == replay.json() == readback.json()
    assert readback.json()["generation"] == "UNPROJECTED"

    with database.session_scope() as session:
        audits = (
            session.query(AuditEvent)
            .filter_by(
                tenant_id=faker_tenant["tenant_id"],
                entity="sheet_binding",
                entity_id=store_id,
            )
            .all()
        )
        assert [row.action for row in audits] == ["SHEET_BIND_CREATE"]


def test_rebind_requires_cas_reason_and_is_replay_safe(client, faker_tenant) -> None:
    store_id = faker_tenant["store_id"]
    assert client.put(
        f"/sheet-bindings/{store_id}",
        headers=ADMIN,
        json=_body("sheet-old"),
    ).status_code == 200

    missing_cas = client.put(
        f"/sheet-bindings/{store_id}",
        headers=ADMIN,
        json=_body("sheet-new", reason="replace stale workbook"),
    )
    short_reason = client.put(
        f"/sheet-bindings/{store_id}",
        headers=ADMIN,
        json=_body("sheet-new", expected="sheet-old", reason="no"),
    )
    changed_tabs_without_cas = client.put(
        f"/sheet-bindings/{store_id}",
        headers=ADMIN,
        json=_body("sheet-old", grila="Grila v2", pontaj="Pontaj", reason="new tabs"),
    )

    assert missing_cas.status_code == 409, missing_cas.text
    assert missing_cas.json()["details"]["code"] == "SHEET_BINDING_STALE"
    assert short_reason.status_code == 400, short_reason.text
    assert short_reason.json()["details"]["code"] == "SHEET_REBIND_REASON_REQUIRED"
    assert changed_tabs_without_cas.status_code == 409, changed_tabs_without_cas.text

    request = _body(
        "sheet-new",
        grila="Grila v2",
        pontaj="Pontaj v2",
        expected="sheet-old",
        reason="replace stale workbook",
    )
    replaced = client.put(f"/sheet-bindings/{store_id}", headers=ADMIN, json=request)
    replay_after_lost_response = client.put(
        f"/sheet-bindings/{store_id}", headers=ADMIN, json=request
    )

    assert replaced.status_code == 200, replaced.text
    assert replay_after_lost_response.status_code == 200, replay_after_lost_response.text
    assert replaced.json() == replay_after_lost_response.json()
    assert replaced.json()["spreadsheet_id"] == "sheet-new"
    assert replaced.json()["sheet_name_grila"] == "Grila v2"
    assert replaced.json()["generation"] == "UNPROJECTED"

    with database.session_scope() as session:
        audits = (
            session.query(AuditEvent)
            .filter_by(
                tenant_id=faker_tenant["tenant_id"],
                entity="sheet_binding",
                entity_id=store_id,
            )
            .order_by(AuditEvent.id)
            .all()
        )
        assert [row.action for row in audits] == ["SHEET_BIND_CREATE", "SHEET_BIND_REPLACE"]
        payload = json.loads(audits[-1].payload)
        assert payload["reason"] == "replace stale workbook"
        assert payload["before"]["spreadsheet_id"] == "sheet-old"
        assert payload["after"]["spreadsheet_id"] == "sheet-new"


def test_spreadsheet_identity_cannot_be_shared_between_stores(client, faker_tenant) -> None:
    first = client.put(
        f"/sheet-bindings/{faker_tenant['store_id']}",
        headers=ADMIN,
        json=_body("sheet-exclusive"),
    )
    second = client.put(
        f"/sheet-bindings/{faker_tenant['other_store_id']}",
        headers=ADMIN,
        json=_body("sheet-exclusive"),
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert second.json()["details"]["code"] == "SHEET_SPREADSHEET_ALREADY_BOUND"


def test_database_enforces_global_spreadsheet_uniqueness(session, faker_tenant) -> None:
    session.add_all(
        [
            SheetBinding(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["store_id"],
                spreadsheet_id="sheet-db-exclusive",
                sheet_name_grila="Grila",
                sheet_name_pontaj="Pontaj",
                generation="UNPROJECTED",
            ),
            SheetBinding(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["other_store_id"],
                spreadsheet_id="sheet-db-exclusive",
                sheet_name_grila="Grila",
                sheet_name_pontaj="Pontaj",
                generation="UNPROJECTED",
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
