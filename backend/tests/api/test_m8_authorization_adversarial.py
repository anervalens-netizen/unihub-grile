"""VAL-006 adversarial authorization and scope proofs.

The suite complements the route-family tests by attacking the boundaries that
would have the highest impact if they failed: tenant identity spoofing,
cross-tenant store IDs, manager attempts to enqueue admin-only exports, and
person-IDOR writes through the canonical Program mutation surface.
"""

from __future__ import annotations

from datetime import date

from ugrile.core import database
from ugrile.domain.enums import MonthState
from ugrile.repositories.models import (
    AuditEvent,
    ExportRun,
    ManagerScope,
    OutboxJob,
    SheetBinding,
    SiteDayAssignment,
    StoreAssignment,
)
from ugrile.repositories.months import MonthRepository


def _headers(*, user_id: str, tenant_id: str) -> dict[str, str]:
    return {
        "X-Ugrile-Identity": user_id,
        "X-Ugrile-Tenant": tenant_id,
    }


def _open_month(tenant_id: str) -> str:
    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(tenant_id, 2026, 8)
        month.state = MonthState.OPEN.value
        session.commit()
        return month.id


def test_dev_identity_headers_cannot_spoof_another_tenant(
    client,
    faker_tenant,
    faker_second_tenant,
) -> None:
    attempts = [
        _headers(
            user_id=faker_tenant["user_id"],
            tenant_id=faker_second_tenant["tenant_id"],
        ),
        _headers(
            user_id=faker_second_tenant["user_id"],
            tenant_id=faker_tenant["tenant_id"],
        ),
    ]

    for headers in attempts:
        response = client.get("/session", headers=headers)
        assert response.status_code == 401, response.text
        assert response.json()["code"] == "AUTH_ERROR"


def test_export_denials_happen_before_any_job_or_export_side_effect(
    client,
    faker_tenant,
    faker_second_tenant,
) -> None:
    month_id = _open_month(faker_tenant["tenant_id"])
    admin = _headers(
        user_id=faker_tenant["user_id"],
        tenant_id=faker_tenant["tenant_id"],
    )
    manager = _headers(
        user_id="user_manager",
        tenant_id=faker_tenant["tenant_id"],
    )

    cross_tenant_store = client.post(
        f"/months/{month_id}/export/store",
        json={
            "store_id": faker_second_tenant["store_id"],
            "idempotency_key": "val006-cross-tenant-store",
        },
        headers=admin,
    )
    manager_create = client.post(
        f"/months/{month_id}/export/store",
        json={
            "store_id": faker_tenant["store_id"],
            "idempotency_key": "val006-manager-create",
        },
        headers=manager,
    )

    assert cross_tenant_store.status_code == 403, cross_tenant_store.text
    assert manager_create.status_code == 403, manager_create.text
    assert manager_create.json()["details"]["capability"] == "export.create"

    with database.session_scope() as session:
        assert session.query(ExportRun).count() == 0
        assert session.query(OutboxJob).count() == 0


def test_sheet_binding_cross_tenant_store_idor_is_side_effect_free(
    client,
    faker_tenant,
    faker_second_tenant,
) -> None:
    response = client.put(
        f"/sheet-bindings/{faker_second_tenant['store_id']}",
        json={
            "spreadsheet_id": "val006-cross-tenant-sheet",
            "sheet_name_grila": "Grila",
            "sheet_name_pontaj": "Pontaj",
        },
        headers=_headers(
            user_id=faker_tenant["user_id"],
            tenant_id=faker_tenant["tenant_id"],
        ),
    )

    assert response.status_code == 404, response.text
    assert response.json()["details"]["code"] == "STORE_NOT_FOUND"
    with database.session_scope() as session:
        assert session.query(SheetBinding).count() == 0
        assert session.query(AuditEvent).count() == 0


def test_program_person_idor_does_not_change_revision_assignments_or_audit(
    client,
    faker_tenant,
    faker_second_tenant,
) -> None:
    month_id = _open_month(faker_tenant["tenant_id"])
    with database.session_scope() as session:
        session.add(
            ManagerScope(
                tenant_id=faker_tenant["tenant_id"],
                user_id="user_manager",
                store_id=faker_tenant["store_id"],
                effective_from=date(2026, 8, 1),
                effective_to=date(2026, 8, 31),
            )
        )
        session.add(
            StoreAssignment(
                tenant_id=faker_tenant["tenant_id"],
                person_id=faker_tenant["person_c_id"],
                store_id=faker_tenant["other_store_id"],
                effective_from=date(2026, 8, 1),
                effective_to=date(2026, 8, 31),
            )
        )
        session.commit()

    manager = _headers(
        user_id="user_manager",
        tenant_id=faker_tenant["tenant_id"],
    )
    base_payload = {
        "business_date": "2026-08-01",
        "status": "WORKING",
        "store_id": faker_tenant["store_id"],
        "working_kind": "NORMAL",
    }

    same_tenant_out_of_scope = client.post(
        f"/months/{month_id}/program/cell",
        params={"expected_revision": 0},
        json={**base_payload, "person_id": faker_tenant["person_c_id"]},
        headers=manager,
    )
    cross_tenant_person = client.post(
        f"/months/{month_id}/program/cell",
        params={"expected_revision": 0},
        json={**base_payload, "person_id": faker_second_tenant["person_id"]},
        headers=manager,
    )

    assert same_tenant_out_of_scope.status_code == 403, same_tenant_out_of_scope.text
    assert cross_tenant_person.status_code == 422, cross_tenant_person.text

    with database.session_scope() as session:
        month = MonthRepository(session).get(month_id)
        assert month.revision == 0
        assert session.query(SiteDayAssignment).count() == 0
        assert session.query(AuditEvent).count() == 0
