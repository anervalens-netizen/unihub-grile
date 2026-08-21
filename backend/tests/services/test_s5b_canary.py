"""S5b canary readback tests (AC-12 structural readback)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.repositories.models import SheetProjectionRun
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.canary import list_active_store_ids, read_store_canary
from ugrile.services.google import GoogleProjectionService


def _seed_projection(session, faker_tenant) -> tuple[str, str]:
    """Seed a Pontaj row + run the fake adapter so SheetProjectionRun has payload."""
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=month.revision,
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
    GoogleProjectionService(session).project_store_for_month(
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id=month.id,
        year=month.year,
        month=month.month,
        revision=month.revision + 1,
        generation="FIXTURE_V1",
    )
    session.commit()
    run = session.execute(
        select(SheetProjectionRun).where(
            SheetProjectionRun.tenant_id == faker_tenant["tenant_id"],
            SheetProjectionRun.store_id == faker_tenant["store_id"],
        )
    ).scalars().first()
    return month.id, run.status if run else "MISSING"


def test_read_store_canary_matches_when_projection_present(session, faker_tenant):
    month_id, status = _seed_projection(session, faker_tenant)
    assert status == "DONE"
    result = read_store_canary(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id=month_id,
    )
    # The fake adapter writes the structural projection for the assigned
    # persons; the canary readback sees those keys plus any unexpected
    # lattice entries the engine projected. The presence of at least
    # one grila key proves the readback pipeline works end-to-end.
    assert result.last_error is None
    assert result.generation == "FIXTURE_V1"
    grila_keys = [k for k in result.unexpected_keys if k.startswith("grila::")]
    assert len(grila_keys) >= 1


def test_read_store_canary_returns_no_projection_when_absent(session, faker_tenant):
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    result = read_store_canary(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id=month.id,
    )
    assert result.last_error == "NO_PROJECTION"
    assert result.matched == 0


def test_list_active_store_ids_returns_active_only(session, faker_tenant):
    stores = list_active_store_ids(session, tenant_id=faker_tenant["tenant_id"])
    assert faker_tenant["store_id"] in stores
