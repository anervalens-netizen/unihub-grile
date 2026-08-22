from __future__ import annotations

from ugrile.domain.enums import MonthState
from ugrile.repositories.months import MonthRepository

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}


def _closed_month_id(faker_tenant) -> str:
    from ugrile.core import database

    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
        month.state = MonthState.CLOSED
        month.revision = 7
        session.commit()
        return month.id


def test_closed_month_rejects_grid_recompute(engine, faker_tenant, client):
    month_id = _closed_month_id(faker_tenant)

    response = client.post(f"/months/{month_id}/grid/compute", headers=ADMIN)
    assert response.status_code == 409, response.text
    assert response.json()["details"]["code"] == "MONTH_CLOSED"


def test_closed_month_rejects_payroll_master_write(engine, faker_tenant, client):
    month_id = _closed_month_id(faker_tenant)

    response = client.post(
        f"/months/{month_id}/salary",
        headers=ADMIN,
        json={
            "person_id": faker_tenant["person_a_id"],
            "effective_from": "2026-01-01",
            "effective_to": None,
            "salary": "2600",
            "tickets": "480",
            "flip": "0",
            "source": "HR_MASTER",
            "notes": None,
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["details"]["code"] == "MONTH_CLOSED"


def test_closed_month_rejects_epay_close_input_write(engine, faker_tenant, client):
    month_id = _closed_month_id(faker_tenant)

    response = client.post(
        f"/months/{month_id}/epay/readback",
        headers=ADMIN,
        json={
            "store_id": faker_tenant["store_id"],
            "observations": [
                {
                    "person_id": faker_tenant["person_a_id"],
                    "category": "UNDER_50",
                    "value": 1,
                },
                {
                    "person_id": faker_tenant["person_a_id"],
                    "category": "AT_OR_OVER_50",
                    "value": 2,
                },
            ],
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["details"]["code"] == "MONTH_CLOSED"


def test_closed_month_still_allows_downstream_sheet_projection(engine, faker_tenant, client):
    """Freeze financial inputs without blocking delivery of already-closed output."""

    month_id = _closed_month_id(faker_tenant)

    response = client.post(
        f"/months/{month_id}/sheet-projection/enqueue",
        headers=ADMIN,
        json={"store_id": faker_tenant["store_id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "GOOGLE_PROJECTION_STORE"
