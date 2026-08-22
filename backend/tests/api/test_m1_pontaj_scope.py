from __future__ import annotations

from datetime import date
from decimal import Decimal

from ugrile.core import database
from ugrile.domain.enums import MonthState
from ugrile.repositories.models import (
    ManagerScope,
    Person,
    PontajProjection,
    StoreAssignment,
)
from ugrile.repositories.months import MonthRepository

MANAGER = {"X-Ugrile-Identity": "user_manager", "X-Ugrile-Tenant": "tenant_acme"}


def test_pontaj_totals_scopes_off_leave_by_effective_home_store(
    client, faker_tenant
):
    """OFF/LEAVE rows cannot bypass manager person scope.

    Current catalog home stores are deliberately made misleading; effective
    ``StoreAssignment`` history is authoritative for the requested date.
    """

    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
        month.state = MonthState.OPEN
        month.revision = 7
        session.add(
            ManagerScope(
                tenant_id=faker_tenant["tenant_id"],
                user_id="user_manager",
                store_id=faker_tenant["store_id"],
                effective_from=date(2026, 8, 1),
                effective_to=date(2026, 8, 31),
            )
        )

        alice = session.get(Person, faker_tenant["person_a_id"])
        assert alice is not None
        alice.home_store_id = faker_tenant["other_store_id"]
        session.add(
            StoreAssignment(
                tenant_id=faker_tenant["tenant_id"],
                person_id=faker_tenant["person_a_id"],
                store_id=faker_tenant["store_id"],
                effective_from=date(2026, 8, 1),
                effective_to=date(2026, 8, 31),
            )
        )

        carmen = session.get(Person, faker_tenant["person_c_id"])
        assert carmen is not None
        carmen.home_store_id = faker_tenant["store_id"]
        session.add(
            StoreAssignment(
                tenant_id=faker_tenant["tenant_id"],
                person_id=faker_tenant["person_c_id"],
                store_id=faker_tenant["other_store_id"],
                effective_from=date(2026, 8, 1),
                effective_to=date(2026, 8, 31),
            )
        )

        for person_id in (faker_tenant["person_a_id"], faker_tenant["person_c_id"]):
            session.add_all(
                [
                    PontajProjection(
                        tenant_id=faker_tenant["tenant_id"],
                        month_id=month.id,
                        person_id=person_id,
                        business_date=date(2026, 8, 10),
                        revision=7,
                        status="OFF",
                        start_time=None,
                        end_time=None,
                        pause_minutes=0,
                        hours=Decimal("0"),
                    ),
                    PontajProjection(
                        tenant_id=faker_tenant["tenant_id"],
                        month_id=month.id,
                        person_id=person_id,
                        business_date=date(2026, 8, 11),
                        revision=7,
                        status="LEAVE",
                        start_time=None,
                        end_time=None,
                        pause_minutes=0,
                        hours=Decimal("0"),
                    ),
                ]
            )
        session.commit()
        month_id = month.id

    response = client.get(f"/months/{month_id}/pontaj-totals", headers=MANAGER)
    assert response.status_code == 200, response.text
    totals = response.json()["totals"]
    assert set(totals) == {faker_tenant["person_a_id"]}
    assert totals[faker_tenant["person_a_id"]]["off_days"] == 1
    assert totals[faker_tenant["person_a_id"]]["leave_days"] == 1


def test_pontaj_totals_fail_closed_when_assignment_history_has_a_gap(client, faker_tenant):
    """Known dated history with an uncovered day must not fall back to current home store."""

    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
        month.state = MonthState.OPEN
        month.revision = 8
        session.add(
            ManagerScope(
                tenant_id=faker_tenant["tenant_id"],
                user_id="user_manager",
                store_id=faker_tenant["store_id"],
                effective_from=date(2026, 8, 1),
                effective_to=date(2026, 8, 31),
            )
        )
        alice = session.get(Person, faker_tenant["person_a_id"])
        assert alice is not None
        alice.home_store_id = faker_tenant["store_id"]
        session.add(
            StoreAssignment(
                tenant_id=faker_tenant["tenant_id"],
                person_id=faker_tenant["person_a_id"],
                store_id=faker_tenant["store_id"],
                effective_from=date(2026, 8, 1),
                effective_to=date(2026, 8, 9),
            )
        )
        session.add(
            PontajProjection(
                tenant_id=faker_tenant["tenant_id"],
                month_id=month.id,
                person_id=faker_tenant["person_a_id"],
                business_date=date(2026, 8, 10),
                revision=8,
                status="OFF",
                start_time=None,
                end_time=None,
                pause_minutes=0,
                hours=Decimal("0"),
            )
        )
        session.commit()
        month_id = month.id

    response = client.get(f"/months/{month_id}/pontaj-totals", headers=MANAGER)
    assert response.status_code == 200, response.text
    assert response.json()["totals"] == {}