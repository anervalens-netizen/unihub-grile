"""GS-006/GS-009 proofs for month-scoped Sheet reconciliation metadata."""

from __future__ import annotations

import json
from datetime import date

from ugrile.connectors.google import write_store_projection
from ugrile.core import database
from ugrile.domain.enums import RoleName
from ugrile.repositories.models import ManagerScope, SheetProjectionRun, User
from ugrile.repositories.months import MonthRepository

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}
MANAGER = {"X-Ugrile-Identity": "user_manager", "X-Ugrile-Tenant": "tenant_acme"}


def _month_id(faker_tenant, year: int, month: int) -> str:
    with database.session_scope() as session:
        return MonthRepository(session).get_or_create(
            faker_tenant["tenant_id"],
            year,
            month,
        ).id


def _payload(month_id: str, store_id: str, *, revision: int = 7) -> dict[str, object]:
    return {
        "metadata": {
            "store_id": store_id,
            "month_id": month_id,
            "year": 2026,
            "month": 8,
            "revision": revision,
            "rule_pack_version": "v-test",
            "projected_at": "2026-08-23T12:00:00+00:00",
        },
        "grila": {"revision": revision, "rows": []},
        "pontaj": {"revision": revision, "rows": []},
    }


def _write_fake(faker_tenant, month_id: str, *, generation: str = "g-reconciled") -> None:
    with database.session_scope() as session:
        write_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            generation=generation,
            payload=_payload(month_id, faker_tenant["store_id"]),
        )


def test_reconciliation_endpoint_exposes_verified_fake_metadata(client, faker_tenant) -> None:
    month_id = _month_id(faker_tenant, 2026, 8)
    _write_fake(faker_tenant, month_id)

    response = client.get(
        f"/months/{month_id}/sheet-reconciliation?store_id={faker_tenant['store_id']}",
        headers=ADMIN,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["verified"] is True
    assert body["verification_mode"] == "fake_local"
    assert body["format_version"] == "v2"
    assert body["generation"] == "g-reconciled"
    assert body["revision"] == 7
    assert body["rule_pack_version"] == "v-test"
    assert body["projected_at"] == "2026-08-23T12:00:00+00:00"
    assert body["grila_rows"] == 0
    assert body["pontaj_rows"] == 0
    assert len(body["grila_checksum_sha256"]) == 64
    assert len(body["pontaj_checksum_sha256"]) == 64
    assert len(body["projection_checksum_sha256"]) == 64


def test_month_a_projection_is_not_returned_for_month_b(client, faker_tenant) -> None:
    month_a = _month_id(faker_tenant, 2026, 8)
    month_b = _month_id(faker_tenant, 2026, 9)
    _write_fake(faker_tenant, month_a, generation="g-august")
    store_id = faker_tenant["store_id"]

    reconciliation = client.get(
        f"/months/{month_b}/sheet-reconciliation?store_id={store_id}",
        headers=ADMIN,
    )
    projection = client.get(
        f"/months/{month_b}/sheet-projection?store_id={store_id}",
        headers=ADMIN,
    )

    assert reconciliation.status_code == 200, reconciliation.text
    assert reconciliation.json()["available"] is False
    assert reconciliation.json()["generation"] is None
    assert projection.status_code == 200, projection.text
    assert projection.json()["generation"] == ""
    assert projection.json()["last_success_generation"] is None
    assert projection.json()["payload"] is None


def test_legacy_snapshot_without_month_identity_fails_closed(client, faker_tenant) -> None:
    month_id = _month_id(faker_tenant, 2026, 8)
    with database.session_scope() as session:
        session.add(
            SheetProjectionRun(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["store_id"],
                status="DONE",
                last_error=None,
                last_success_generation="legacy-g1",
                payload=json.dumps(
                    {
                        "grila": {"revision": 1, "rows": []},
                        "pontaj": {"revision": 1, "rows": []},
                        "reconciliation": {"format_version": "v1", "verified": True},
                    }
                ),
                generation="legacy-g1",
                failures=0,
            )
        )

    response = client.get(
        f"/months/{month_id}/sheet-reconciliation?store_id={faker_tenant['store_id']}",
        headers=ADMIN,
    )

    assert response.status_code == 200, response.text
    assert response.json()["available"] is False
    assert response.json()["verified"] is False


def test_reconciliation_requires_sheet_read_and_exact_manager_store_scope(
    client,
    faker_tenant,
) -> None:
    month_id = _month_id(faker_tenant, 2026, 8)
    with database.session_scope() as session:
        session.add(
            User(
                id="user_manager",
                tenant_id=faker_tenant["tenant_id"],
                email="manager@acme.example",
                display_name="Manager",
                role=RoleName.MANAGER.value,
            )
        )
        session.flush()
        session.add(
            ManagerScope(
                tenant_id=faker_tenant["tenant_id"],
                user_id="user_manager",
                store_id=faker_tenant["store_id"],
                effective_from=date(2026, 8, 1),
                effective_to=date(2026, 8, 31),
            )
        )

    allowed = client.get(
        f"/months/{month_id}/sheet-reconciliation?store_id={faker_tenant['store_id']}",
        headers=MANAGER,
    )
    denied = client.get(
        f"/months/{month_id}/sheet-reconciliation?store_id={faker_tenant['other_store_id']}",
        headers=MANAGER,
    )

    assert allowed.status_code == 200, allowed.text
    assert denied.status_code == 403, denied.text
