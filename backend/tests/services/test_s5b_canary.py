"""S5b canary readback tests (AC-12 structural readback).

The canary key contract is documented in
``ugrile.services.canary``. For every assertion the test compares the
actual keys produced by the fake adapter against the expected lattice
built from the canonical ``SiteDayAssignment`` (WORKING) and
``PontajProjection`` rows at the projection's calendar revision.
"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select

from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.repositories.models import SheetProjectionRun
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.canary import list_active_store_ids, read_store_canary
from ugrile.services.google import GoogleProjectionService


def _seed_projection(session, faker_tenant) -> tuple[str, str]:
    """Seed a Pontaj row + run the fake adapter so SheetProjectionRun has payload.

    The calendar CAS increments ``month.revision`` to 1; the projection
    is invoked at that same revision so the PontajProjection rows the CAS
    just produced are visible to the adapter. Earlier revisions of this
    helper called the projection one revision ahead, which left the
    projection with no PontajProjection rows and could not satisfy the
    structural lattice.
    """
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
        revision=month.revision,
        generation="FIXTURE_V1",
    )
    session.commit()
    run = (
        session.execute(
            select(SheetProjectionRun).where(
                SheetProjectionRun.tenant_id == faker_tenant["tenant_id"],
                SheetProjectionRun.store_id == faker_tenant["store_id"],
            )
        )
        .scalars()
        .first()
    )
    return month.id, run.status if run else "MISSING"


def _tamper_drop_last_grila_row(session, faker_tenant) -> None:
    """Drop the last Grila row from the persisted payload and persist in place.

    The fake adapter is the sole writer of ``sheet_projection_runs.payload``
    at S5b, but the canary is read-only: tampering the persisted JSON is
    the only negative-path probe without a live Sheet authority.
    """
    run = (
        session.execute(
            select(SheetProjectionRun).where(
                SheetProjectionRun.tenant_id == faker_tenant["tenant_id"],
                SheetProjectionRun.store_id == faker_tenant["store_id"],
            )
        )
        .scalars()
        .first()
    )
    assert run is not None
    payload = json.loads(run.payload or "{}")
    assert isinstance(payload, dict)
    grila = payload.get("grila")
    assert isinstance(grila, dict)
    rows = grila.get("rows")
    assert isinstance(rows, list) and rows, "tamper setup expects a populated grila"
    grila["rows"] = rows[:-1]
    run.payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    session.commit()


def test_read_store_canary_ok_on_clean_seed(session, faker_tenant):
    month_id, status = _seed_projection(session, faker_tenant)
    assert status == "DONE"
    result = read_store_canary(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id=month_id,
    )
    # Positive path: the structural projection matches the canonical
    # lattice exactly. ``ok`` must be True with the new key contract;
    # any matching failure means the contract is broken.
    assert result.last_error is None
    assert result.generation == "FIXTURE_V1"
    assert result.ok is True
    assert result.missing_keys == []
    assert result.unexpected_keys == []
    assert result.matched > 0


def test_read_store_canary_fails_on_tampered_payload(session, faker_tenant):
    month_id, _ = _seed_projection(session, faker_tenant)
    _tamper_drop_last_grila_row(session, faker_tenant)
    result = read_store_canary(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id=month_id,
    )
    # Negative path: deleting one Grila row must surface a non-empty
    # ``missing_keys`` set and break ``ok``. The relaxed
    # ``len(unexpected_keys) >= 1`` check is intentionally NOT used as
    # a substitute for ``ok=True``.
    assert result.last_error is None
    assert result.ok is False
    assert result.missing_keys, "tampered payload must drop at least one expected key"
    assert all(key.startswith("grila::") for key in result.missing_keys)


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
