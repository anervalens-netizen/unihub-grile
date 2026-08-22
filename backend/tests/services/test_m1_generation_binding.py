from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.repositories.models import Person, SalesStoreDay, StoreTarget
from ugrile.repositories.months import MonthRepository
from ugrile.repositories.salary import SalaryRepository
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.financial_inputs import financial_input_mismatch
from ugrile.services.grid import GridService


def test_generation_drift_blocks_then_recomputes_without_calendar_edit(
    session, faker_tenant
):
    """A new sales generation is stale state until exact-generation recompute."""

    tenant_id = faker_tenant["tenant_id"]
    store_id = faker_tenant["store_id"]
    person_id = faker_tenant["person_a_id"]

    session.add(
        SalesStoreDay(
            tenant_id=tenant_id,
            store_id=store_id,
            business_date=date(2026, 8, 1),
            generation="GEN_001",
            amount=Decimal("100.00"),
            currency="RON",
            sim_quantity=1,
        )
    )
    session.add(
        StoreTarget(
            tenant_id=tenant_id,
            store_id=store_id,
            year=2026,
            month=8,
            kind="MONTHLY_SALES",
            version=1,
            amount=Decimal("3100.00"),
            currency="RON",
            sales_days=31,
        )
    )
    SalaryRepository(session).upsert_window(
        tenant_id=tenant_id,
        person_id=person_id,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        salary=Decimal("2600.00"),
        tickets=Decimal("480.00"),
        flip=Decimal("0.00"),
    )
    session.commit()

    month = MonthRepository(session).get_or_create(tenant_id, 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    CalendarService(session).apply(
        month=month,
        tenant_id=tenant_id,
        expected_revision=0,
        changes=[
            CalendarChange(
                person_id,
                date(2026, 8, 1),
                store_id,
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    session.commit()

    _, rows = GridService(session).compute_and_persist(
        tenant_id=tenant_id,
        month=month,
        sales_generation="GEN_001",
    )
    session.commit()
    old_row = next(item for item in rows if item.person_id == person_id)

    # New authoritative generation, deliberately with identical financial values.
    session.add(
        SalesStoreDay(
            tenant_id=tenant_id,
            store_id=store_id,
            business_date=date(2026, 8, 1),
            generation="GEN_002",
            amount=Decimal("100.00"),
            currency="RON",
            sim_quantity=1,
        )
    )
    session.commit()

    person = session.get(Person, person_id)
    assert person is not None
    mismatch = financial_input_mismatch(
        session,
        tenant_id=tenant_id,
        month=month,
        person=person,
        row=old_row,
    )
    assert mismatch is not None
    assert "sales changed" in mismatch
    assert "generation changed" in mismatch
    assert "grid=GEN_001, current=GEN_002" in mismatch

    # Recompute at the same calendar revision using the new physical generation.
    # No calendar edit/re-attribution is required to recover from connector drift.
    original_revision = month.revision
    _, refreshed_rows = GridService(session).compute_and_persist(
        tenant_id=tenant_id,
        month=month,
        sales_generation="GEN_002",
    )
    session.commit()
    session.refresh(month)
    assert month.revision == original_revision
    refreshed = next(item for item in refreshed_rows if item.person_id == person_id)
    payload = json.loads(refreshed.payload)
    assert payload["inputs"]["sales_generation"] == "GEN_002"
    assert Decimal(payload["inputs"]["calendar"][0]["sales_amount"]) == Decimal("100.00")
    assert (
        financial_input_mismatch(
            session,
            tenant_id=tenant_id,
            month=month,
            person=person,
            row=refreshed,
        )
        is None
    )
