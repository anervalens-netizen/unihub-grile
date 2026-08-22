from __future__ import annotations

from datetime import date

from ugrile.core import database
from ugrile.domain.enums import MonthState
from ugrile.repositories.models import (
    ManagerScope,
    Person,
    SiteDayAssignment,
    StoreAssignment,
)
from ugrile.repositories.months import MonthRepository

MANAGER = {"X-Ugrile-Identity": "user_manager", "X-Ugrile-Tenant": "tenant_acme"}


def _open_month(session, faker_tenant) -> str:
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    return month.id


def _scope_manager(session, faker_tenant, *, start_day: int = 1, end_day: int = 31) -> None:
    session.add(
        ManagerScope(
            tenant_id=faker_tenant["tenant_id"],
            user_id="user_manager",
            store_id=faker_tenant["store_id"],
            effective_from=date(2026, 8, start_day),
            effective_to=date(2026, 8, end_day),
        )
    )


def test_program_people_uses_effective_home_not_mutable_current_home(client, faker_tenant):
    """A later catalog transfer cannot hide/include historical Program people."""

    with database.session_scope() as session:
        month_id = _open_month(session, faker_tenant)
        _scope_manager(session, faker_tenant)

        alice = session.get(Person, faker_tenant["person_a_id"])
        carmen = session.get(Person, faker_tenant["person_c_id"])
        assert alice is not None and carmen is not None

        # Deliberately make the current catalog values opposite to the
        # effective-dated August authority.
        alice.home_store_id = faker_tenant["other_store_id"]
        carmen.home_store_id = faker_tenant["store_id"]
        session.add_all(
            [
                StoreAssignment(
                    tenant_id=faker_tenant["tenant_id"],
                    person_id=faker_tenant["person_a_id"],
                    store_id=faker_tenant["store_id"],
                    effective_from=date(2026, 8, 1),
                    effective_to=date(2026, 8, 31),
                ),
                StoreAssignment(
                    tenant_id=faker_tenant["tenant_id"],
                    person_id=faker_tenant["person_c_id"],
                    store_id=faker_tenant["other_store_id"],
                    effective_from=date(2026, 8, 1),
                    effective_to=date(2026, 8, 31),
                ),
            ]
        )
        session.commit()

    response = client.get(
        f"/months/{month_id}/program?perspective=people",
        headers=MANAGER,
    )
    assert response.status_code == 200, response.text
    rows = {row["row_id"]: row for row in response.json()["rows"]}
    assert faker_tenant["person_a_id"] in rows
    assert faker_tenant["person_c_id"] not in rows
    assert rows[faker_tenant["person_a_id"]]["home_store_id"] == faker_tenant["store_id"]
    assert all(cell["locked"] is False for cell in rows[faker_tenant["person_a_id"]]["cells"])


def test_program_store_locked_dates_redact_assignment_identity(client, faker_tenant):
    """A visible store row must not leak people from dates outside manager scope."""

    with database.session_scope() as session:
        month_id = _open_month(session, faker_tenant)
        _scope_manager(session, faker_tenant, start_day=1, end_day=15)
        session.add(
            SiteDayAssignment(
                tenant_id=faker_tenant["tenant_id"],
                month_id=month_id,
                store_id=faker_tenant["store_id"],
                person_id=faker_tenant["person_a_id"],
                business_date=date(2026, 8, 20),
                status="WORKING",
                working_kind="NORMAL",
                revision=1,
                source="TEST_SCOPE",
            )
        )
        session.commit()

    response = client.get(
        f"/months/{month_id}/program?perspective=stores",
        headers=MANAGER,
    )
    assert response.status_code == 200, response.text
    row = next(row for row in response.json()["rows"] if row["row_id"] == faker_tenant["store_id"])
    cell = next(cell for cell in row["cells"] if cell["business_date"] == "2026-08-20")
    assert cell["locked"] is True
    assert cell["status"] == "LOCKED"
    assert cell["badge"] == "BLOCAT"
    assert cell["person_id"] is None
    assert cell["display_name"] is None
    assert cell["home_store_id"] is None


def test_program_people_redacts_assignment_to_out_of_scope_store(client, faker_tenant):
    """Person scope alone cannot reveal the external store where that person works."""

    with database.session_scope() as session:
        month_id = _open_month(session, faker_tenant)
        _scope_manager(session, faker_tenant)
        session.add(
            SiteDayAssignment(
                tenant_id=faker_tenant["tenant_id"],
                month_id=month_id,
                store_id=faker_tenant["other_store_id"],
                person_id=faker_tenant["person_a_id"],
                business_date=date(2026, 8, 10),
                status="WORKING",
                working_kind="EXTRA_OTHER",
                revision=1,
                source="TEST_SCOPE",
            )
        )
        session.commit()

    response = client.get(
        f"/months/{month_id}/program?perspective=people",
        headers=MANAGER,
    )
    assert response.status_code == 200, response.text
    row = next(
        row
        for row in response.json()["rows"]
        if row["row_id"] == faker_tenant["person_a_id"]
    )
    cell = next(cell for cell in row["cells"] if cell["business_date"] == "2026-08-10")
    assert cell["locked"] is True
    assert cell["status"] == "LOCKED"
    assert cell["badge"] == "BLOCAT"
    assert cell["store_id"] is None
    assert cell["display_name"] is None
    assert cell["home_store_id"] is None
