from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from ugrile.domain.enums import DayStatus, EpayCategory, MonthState, WorkingKind
from ugrile.domain.rule_pack import RULE_PACK_VERSION
from ugrile.repositories.models import (
    GridCalculation,
    IncentiveInput,
    SalesStoreDay,
    StoreTarget,
)
from ugrile.repositories.months import MonthRepository
from ugrile.repositories.salary import SalaryRepository
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.epay import record_readback
from ugrile.services.financial_inputs import financial_input_mismatch
from ugrile.services.grid import GridService
from ugrile.services.policy_close import PolicyCloseService


def _seed_financial_grid(session, faker_tenant):
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
    session.add(
        IncentiveInput(
            tenant_id=tenant_id,
            person_id=person_id,
            year=2026,
            month=8,
            version=1,
            amount=Decimal("10.00"),
            currency="RON",
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
    row = next(item for item in rows if item.person_id == person_id)
    return month, row


def test_final_close_detects_salary_change_without_calendar_revision(session, faker_tenant):
    month, _ = _seed_financial_grid(session, faker_tenant)
    original_revision = month.revision
    SalaryRepository(session).upsert_window(
        tenant_id=faker_tenant["tenant_id"],
        person_id=faker_tenant["person_a_id"],
        effective_from=date(2026, 1, 1),
        effective_to=None,
        salary=Decimal("3000.00"),
        tickets=Decimal("480.00"),
        flip=Decimal("0.00"),
    )
    session.commit()
    session.refresh(month)
    assert month.revision == original_revision

    blockers = PolicyCloseService(session)._grid_blockers(
        tenant_id=faker_tenant["tenant_id"],
        month=month,
    )
    stale = [
        blocker
        for blocker in blockers
        if blocker.person_id == faker_tenant["person_a_id"]
        and "financial inputs are stale" in blocker.message
    ]
    assert len(stale) == 1
    assert "salary changed" in stale[0].message


def test_financial_guard_detects_new_connector_sales_generation(session, faker_tenant):
    month, row = _seed_financial_grid(session, faker_tenant)
    session.add(
        SalesStoreDay(
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            business_date=date(2026, 8, 1),
            generation="GEN_002",
            amount=Decimal("150.00"),
            currency="RON",
            sim_quantity=2,
        )
    )
    session.commit()

    mismatch = financial_input_mismatch(
        session,
        tenant_id=faker_tenant["tenant_id"],
        month=month,
        person=session.get(type(row).person.property.mapper.class_, faker_tenant["person_a_id"])
        if False
        else session.get(__import__("ugrile.repositories.models", fromlist=["Person"]).Person, faker_tenant["person_a_id"]),
        row=row,
    )
    assert mismatch is not None
    assert "sales changed" in mismatch


def test_financial_guard_detects_target_and_incentive_changes(session, faker_tenant):
    month, row = _seed_financial_grid(session, faker_tenant)
    from ugrile.repositories.models import Person

    person = session.get(Person, faker_tenant["person_a_id"])
    assert person is not None

    session.add(
        StoreTarget(
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            year=2026,
            month=8,
            kind="MONTHLY_SALES",
            version=2,
            amount=Decimal("6200.00"),
            currency="RON",
            sales_days=31,
        )
    )
    session.commit()
    mismatch = financial_input_mismatch(
        session,
        tenant_id=faker_tenant["tenant_id"],
        month=month,
        person=person,
        row=row,
    )
    assert mismatch is not None
    assert "target changed" in mismatch

    session.query(StoreTarget).filter_by(
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        year=2026,
        month=8,
        version=2,
    ).delete()
    session.add(
        IncentiveInput(
            tenant_id=faker_tenant["tenant_id"],
            person_id=faker_tenant["person_a_id"],
            year=2026,
            month=8,
            version=2,
            amount=Decimal("50.00"),
            currency="RON",
        )
    )
    session.commit()
    mismatch = financial_input_mismatch(
        session,
        tenant_id=faker_tenant["tenant_id"],
        month=month,
        person=person,
        row=row,
    )
    assert mismatch is not None
    assert "incentive changed" in mismatch


def test_epay_value_change_invalidates_current_grid_rows(session, faker_tenant):
    tenant_id = faker_tenant["tenant_id"]
    store_id = faker_tenant["store_id"]
    person_id = faker_tenant["person_a_id"]
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
    session.add(
        GridCalculation(
            tenant_id=tenant_id,
            month_id=month.id,
            store_id=store_id,
            person_id=person_id,
            rule_pack_version=RULE_PACK_VERSION,
            revision=month.revision,
            inputs_hash="a" * 64,
            outputs_hash="b" * 64,
            payload=json.dumps({"inputs": {}, "components": {}, "anomalies": []}),
        )
    )
    session.commit()

    result = record_readback(
        session,
        tenant_id=tenant_id,
        month_id=month.id,
        store_id=store_id,
        actor_id="user_admin",
        observations=[
            {
                "person_id": person_id,
                "category": EpayCategory.UNDER_50.value,
                "value": 1,
            },
            {
                "person_id": person_id,
                "category": EpayCategory.AT_OR_OVER_50.value,
                "value": 0,
            },
        ],
    )
    assert result.valid_count == 2
    session.flush()
    remaining = session.query(GridCalculation).filter_by(
        tenant_id=tenant_id,
        month_id=month.id,
        revision=month.revision,
        rule_pack_version=RULE_PACK_VERSION,
    ).count()
    assert remaining == 0


def test_identical_epay_refresh_keeps_current_grid_rows(session, faker_tenant):
    tenant_id = faker_tenant["tenant_id"]
    store_id = faker_tenant["store_id"]
    person_id = faker_tenant["person_a_id"]
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
    session.add(
        GridCalculation(
            tenant_id=tenant_id,
            month_id=month.id,
            store_id=store_id,
            person_id=person_id,
            rule_pack_version=RULE_PACK_VERSION,
            revision=month.revision,
            inputs_hash="a" * 64,
            outputs_hash="b" * 64,
            payload=json.dumps({"inputs": {}, "components": {}, "anomalies": []}),
        )
    )
    session.commit()

    record_readback(
        session,
        tenant_id=tenant_id,
        month_id=month.id,
        store_id=store_id,
        actor_id="user_admin",
        observations=[
            {
                "person_id": person_id,
                "category": EpayCategory.UNDER_50.value,
                "value": 0,
            },
            {
                "person_id": person_id,
                "category": EpayCategory.AT_OR_OVER_50.value,
                "value": 0,
            },
        ],
    )
    session.flush()
    remaining = session.query(GridCalculation).filter_by(
        tenant_id=tenant_id,
        month_id=month.id,
        revision=month.revision,
        rule_pack_version=RULE_PACK_VERSION,
    ).count()
    assert remaining == 1
