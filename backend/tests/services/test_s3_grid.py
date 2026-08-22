"""S3 service tests — grid calculation snapshot (AC-09).

The tests prove:

* The grid service produces a ``GridCalculation`` row per person/month.
* Re-running the same computation produces identical inputs/outputs hashes.
* Same-revision recomputation safely replaces the last-good snapshot.
* Pontaj is bound to the current calendar revision.
* The latest target version is the only target consumed by payroll.
* Closed months cannot be recalculated.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from ugrile.connectors.fixtures import FIXTURE_GENERATION
from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.domain.errors import ConflictError
from ugrile.domain.rule_pack import RULE_PACK_VERSION
from ugrile.repositories.models import GridCalculation, SalesStoreDay, StoreTarget
from ugrile.repositories.months import MonthRepository
from ugrile.repositories.salary import SalaryRepository
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.grid import GridService


def _seed_sales_and_target(session, faker_tenant, store_id, amount, target):
    session.add(
        SalesStoreDay(
            tenant_id=faker_tenant["tenant_id"],
            store_id=store_id,
            business_date=date(2026, 8, 1),
            generation=FIXTURE_GENERATION,
            amount=Decimal(amount),
            currency="RON",
        )
    )
    session.add(
        StoreTarget(
            tenant_id=faker_tenant["tenant_id"],
            store_id=store_id,
            year=2026,
            month=8,
            kind="MONTHLY_SALES",
            version=1,
            amount=Decimal(target),
            currency="RON",
        )
    )
    session.commit()


def _prepare(session, faker_tenant, person_id, store_id, salary=Decimal("2600")):
    _seed_sales_and_target(
        session,
        faker_tenant,
        store_id=store_id,
        amount="12500",
        target="250000",
    )
    SalaryRepository(session).upsert_window(
        tenant_id=faker_tenant["tenant_id"],
        person_id=person_id,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        salary=salary,
        tickets=Decimal("480"),
        flip=Decimal("0"),
        source="HR_MASTER",
    )
    session.commit()
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
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
    return month


def _row_payload(rows, person_id: str) -> dict[str, object]:
    row = next(item for item in rows if item.person_id == person_id)
    return json.loads(row.payload)


def test_grid_service_writes_one_snapshot_per_active_person(session, faker_tenant):
    month = _prepare(
        session,
        faker_tenant,
        faker_tenant["person_a_id"],
        faker_tenant["store_id"],
    )
    snapshots, rows = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    assert len(rows) == 3
    assert len(snapshots) == 3
    assert RULE_PACK_VERSION in {row.rule_pack_version for row in rows}


def test_grid_hash_is_deterministic_across_runs(session, faker_tenant):
    month = _prepare(
        session,
        faker_tenant,
        faker_tenant["person_a_id"],
        faker_tenant["store_id"],
    )
    first_snapshots, first_rows = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    first_hashes = sorted((row.person_id, row.inputs_hash, row.outputs_hash) for row in first_rows)
    session.commit()

    second_snapshots, second_rows = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    second_hashes = sorted((row.person_id, row.inputs_hash, row.outputs_hash) for row in second_rows)
    assert first_hashes == second_hashes
    assert (
        session.query(GridCalculation)
        .filter_by(
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            revision=month.revision,
            rule_pack_version=RULE_PACK_VERSION,
        )
        .count()
        == 3
    )
    _ = (first_snapshots, second_snapshots)


def test_grid_same_revision_recompute_picks_up_salary_change(session, faker_tenant):
    month = _prepare(
        session,
        faker_tenant,
        faker_tenant["person_a_id"],
        faker_tenant["store_id"],
    )
    _, first_rows = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    first_payload = _row_payload(first_rows, faker_tenant["person_a_id"])
    assert Decimal(first_payload["inputs"]["parameters"]["salary"]) == Decimal("2600")
    session.commit()

    SalaryRepository(session).upsert_window(
        tenant_id=faker_tenant["tenant_id"],
        person_id=faker_tenant["person_a_id"],
        effective_from=date(2026, 1, 1),
        effective_to=None,
        salary=Decimal("3000"),
        tickets=Decimal("480"),
        flip=Decimal("0"),
        source="HR_MASTER",
    )
    session.commit()
    session.refresh(month)
    original_revision = month.revision

    _, rows = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    assert month.revision == original_revision
    payload = _row_payload(rows, faker_tenant["person_a_id"])
    assert Decimal(payload["inputs"]["parameters"]["salary"]) == Decimal("3000")
    assert (
        session.query(GridCalculation)
        .filter_by(
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            revision=month.revision,
            rule_pack_version=RULE_PACK_VERSION,
        )
        .count()
        == 3
    )


def test_grid_reads_only_current_pontaj_revision(session, faker_tenant):
    store_id = faker_tenant["store_id"]
    person_id = faker_tenant["person_a_id"]
    session.add_all(
        [
            SalesStoreDay(
                tenant_id=faker_tenant["tenant_id"],
                store_id=store_id,
                business_date=business_date,
                generation=FIXTURE_GENERATION,
                amount=Decimal("100"),
                currency="RON",
            )
            for business_date in (date(2026, 8, 1), date(2026, 8, 2))
        ]
    )
    session.add(
        StoreTarget(
            tenant_id=faker_tenant["tenant_id"],
            store_id=store_id,
            year=2026,
            month=8,
            kind="MONTHLY_SALES",
            version=1,
            amount=Decimal("3100"),
            currency="RON",
            sales_days=31,
        )
    )
    SalaryRepository(session).upsert_window(
        tenant_id=faker_tenant["tenant_id"],
        person_id=person_id,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        salary=Decimal("2600"),
        tickets=Decimal("480"),
        flip=Decimal("0"),
    )
    session.commit()

    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
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
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=1,
        changes=[
            CalendarChange(
                person_id,
                date(2026, 8, 2),
                store_id,
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    session.commit()
    session.refresh(month)
    assert month.revision == 2

    _, rows = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    payload = _row_payload(rows, person_id)
    assert payload["inputs"]["pontaj"]["working_days"] == 2
    assert len(payload["inputs"]["calendar"]) == 2


def test_grid_uses_latest_target_version_even_if_inserted_first(session, faker_tenant):
    store_id = faker_tenant["store_id"]
    person_id = faker_tenant["person_a_id"]
    session.add(
        SalesStoreDay(
            tenant_id=faker_tenant["tenant_id"],
            store_id=store_id,
            business_date=date(2026, 8, 1),
            generation=FIXTURE_GENERATION,
            amount=Decimal("200"),
            currency="RON",
        )
    )
    session.add_all(
        [
            StoreTarget(
                tenant_id=faker_tenant["tenant_id"],
                store_id=store_id,
                year=2026,
                month=8,
                kind="MONTHLY_SALES",
                version=2,
                amount=Decimal("6200"),
                currency="RON",
                sales_days=31,
            ),
            StoreTarget(
                tenant_id=faker_tenant["tenant_id"],
                store_id=store_id,
                year=2026,
                month=8,
                kind="MONTHLY_SALES",
                version=1,
                amount=Decimal("3100"),
                currency="RON",
                sales_days=31,
            ),
        ]
    )
    SalaryRepository(session).upsert_window(
        tenant_id=faker_tenant["tenant_id"],
        person_id=person_id,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        salary=Decimal("2600"),
        tickets=Decimal("480"),
        flip=Decimal("0"),
    )
    session.commit()
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
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
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    payload = _row_payload(rows, person_id)
    assert payload["inputs"]["calendar"][0]["target_amount"] == "200.00"
    assert payload["inputs"]["target_sources"][0]["version"] == 2


def test_grid_recompute_rejects_closed_month(session, faker_tenant):
    month = _prepare(
        session,
        faker_tenant,
        faker_tenant["person_a_id"],
        faker_tenant["store_id"],
    )
    month.state = MonthState.CLOSED
    session.commit()
    with pytest.raises(ConflictError) as exc:
        GridService(session).compute_and_persist(
            tenant_id=faker_tenant["tenant_id"], month=month
        )
    assert exc.value.details["code"] == "MONTH_CLOSED"


def test_grid_reproduces_service_components_for_alice(session, faker_tenant):
    month = _prepare(
        session,
        faker_tenant,
        faker_tenant["person_a_id"],
        faker_tenant["store_id"],
    )
    _, rows = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    alice = next(row for row in rows if row.person_id == faker_tenant["person_a_id"])
    payload = alice.payload
    assert '"main_commission":"375"' in payload
    assert '"main_bonus":"400"' in payload
    assert '"total_salary":"3855"' in payload


def test_grid_snapshot_includes_inputs_and_outputs_hashes(session, faker_tenant):
    month = _prepare(
        session,
        faker_tenant,
        faker_tenant["person_a_id"],
        faker_tenant["store_id"],
    )
    _, rows = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    for row in rows:
        assert len(row.inputs_hash) == 64
        assert len(row.outputs_hash) == 64
        assert row.rule_pack_version == RULE_PACK_VERSION


def test_grid_uses_zero_salary_when_master_missing(session, faker_tenant):
    _seed_sales_and_target(
        session,
        faker_tenant,
        store_id=faker_tenant["store_id"],
        amount="0",
        target="0",
    )
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                faker_tenant["person_a_id"],
                date(2026, 8, 1),
                faker_tenant["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    session.commit()
    _, rows = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    alice = next(row for row in rows if row.person_id == faker_tenant["person_a_id"])
    payload = json.loads(alice.payload)
    assert Decimal(payload["inputs"]["parameters"]["salary"]) == Decimal("0")
    assert Decimal(payload["inputs"]["parameters"]["tickets"]) == Decimal("0")
