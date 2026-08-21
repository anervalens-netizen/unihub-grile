"""S5a API tests — E-pay readback + Google projection readback.

The tests exercise the API surface without any network I/O:

* ``POST /months/{id}/epay/readback`` is admin-only; non-admins get
  403. Valid 0..10 integers are accepted and audited; invalid inputs
  return ``is_valid=False`` rows.
* ``GET /months/{id}/sheet-projection`` returns the structural payload
  the fake adapter persisted; the canary readback never touches Google.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ugrile.domain.enums import DayStatus, WorkingKind
from ugrile.repositories.models import SiteDayAssignment
from ugrile.repositories.months import MonthRepository

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}
MANAGER = {"X-Ugrile-Identity": "user_manager", "X-Ugrile-Tenant": "tenant_acme"}


def _seed_working_month(client, faker_tenant, session):
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    session.add(
        SiteDayAssignment(
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["store_id"],
            person_id=faker_tenant["person_a_id"],
            business_date=datetime(2026, 8, 1, tzinfo=UTC).date(),
            status=DayStatus.WORKING.value,
            working_kind=WorkingKind.NORMAL.value,
        )
    )
    session.commit()
    return month.id


def test_epay_readback_requires_admin(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _seed_working_month(client, faker_tenant, session)

    response = client.post(
        f"/months/{month_id}/epay/readback",
        headers=MANAGER,
        json={
            "store_id": faker_tenant["store_id"],
            "observations": [
                {
                    "person_id": faker_tenant["person_a_id"],
                    "category": "UNDER_50",
                    "value": 1,
                }
            ],
        },
    )
    assert response.status_code == 403, response.text

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
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid_count"] == 2
    assert body["invalid_count"] == 0
    assert {item["category"] for item in body["items"]} == {"UNDER_50", "AT_OR_OVER_50"}


def test_epay_readback_records_invalid_inputs(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _seed_working_month(client, faker_tenant, session)

    response = client.post(
        f"/months/{month_id}/epay/readback",
        headers=ADMIN,
        json={
            "store_id": faker_tenant["store_id"],
            "observations": [
                {"person_id": faker_tenant["person_a_id"], "category": "UNDER_50", "value": ""},
                {"person_id": faker_tenant["person_a_id"], "category": "AT_OR_OVER_50", "value": "abc"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid_count"] == 0
    assert body["invalid_count"] == 2
    by_person = {(item["person_id"], item["category"]): item for item in body["items"]}
    assert by_person[(faker_tenant["person_a_id"], "UNDER_50")]["is_valid"] is False
    assert by_person[(faker_tenant["person_a_id"], "UNDER_50")]["raw_value"] == ""
    assert by_person[(faker_tenant["person_a_id"], "AT_OR_OVER_50")]["raw_value"] == "abc"


def test_epay_readback_rejects_empty(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _seed_working_month(client, faker_tenant, session)

    response = client.post(
        f"/months/{month_id}/epay/readback",
        headers=ADMIN,
        json={"store_id": faker_tenant["store_id"], "observations": []},
    )
    assert response.status_code == 422, response.text


def test_epay_freshness_endpoint(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _seed_working_month(client, faker_tenant, session)

    response = client.get(
        f"/months/{month_id}/epay/freshness",
        params={"store_id": faker_tenant["store_id"]},
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["expected_count"] == 2
    assert body["fresh_count"] == 0
    assert body["is_fresh"] is False


def test_sheet_projection_empty_state(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _seed_working_month(client, faker_tenant, session)

    response = client.get(
        f"/months/{month_id}/sheet-projection",
        params={"store_id": faker_tenant["store_id"]},
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["store_id"] == faker_tenant["store_id"]
    assert body["last_success_generation"] is None
    assert body["payload"] is None
    assert body["failures"] == 0


def test_sheet_projection_enqueue_creates_job(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _seed_working_month(client, faker_tenant, session)

    response = client.post(
        f"/months/{month_id}/sheet-projection/enqueue",
        headers=ADMIN,
        json={"store_id": faker_tenant["store_id"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "GOOGLE_PROJECTION_STORE"
    assert body["status"] == "PENDING"
    # The payload includes month and store ids so the worker can scope.
    from ugrile.core import database as database_mod
    from ugrile.repositories.models import OutboxJob

    with database_mod.session_scope() as session:
        row = session.get(OutboxJob, body["id"])
        assert row is not None
        import json

        payload = json.loads(row.payload)
        assert payload["store_id"] == faker_tenant["store_id"]
        assert payload["month_id"] == month_id


def test_sheet_projection_enqueue_requires_admin(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _seed_working_month(client, faker_tenant, session)

    response = client.post(
        f"/months/{month_id}/sheet-projection/enqueue",
        headers=MANAGER,
        json={"store_id": faker_tenant["store_id"]},
    )
    assert response.status_code == 403, response.text
