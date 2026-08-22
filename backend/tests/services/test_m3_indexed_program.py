from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.repositories.models import Month, PersonDayAbsence
from ugrile.repositories.models import SiteDayAssignment as AssignmentRow
from ugrile.services.overview import ProgramService
from ugrile.services.program_fast import IndexedProgramService


@pytest.mark.parametrize("perspective", ["stores", "people"])
def test_indexed_program_matches_legacy_contract(
    session: Session,
    faker_tenant: dict[str, str],
    perspective: str,
) -> None:
    month = Month(
        id="month_m3_program_equivalence",
        tenant_id=faker_tenant["tenant_id"],
        year=2026,
        month=8,
        state=MonthState.OPEN.value,
        revision=7,
    )
    session.add(month)
    session.flush()
    session.add(
        AssignmentRow(
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["store_id"],
            person_id=faker_tenant["person_a_id"],
            business_date=date(2026, 8, 1),
            status=DayStatus.WORKING.value,
            working_kind=WorkingKind.NORMAL.value,
            revision=month.revision,
            source="M3_EQUIVALENCE",
        )
    )
    session.add(
        PersonDayAbsence(
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            person_id=faker_tenant["person_b_id"],
            business_date=date(2026, 8, 2),
            status=DayStatus.OFF.value,
        )
    )
    session.commit()

    legacy = ProgramService(session).month_grid(
        tenant_id=faker_tenant["tenant_id"],
        month=month,
        perspective=perspective,
        manager_scope_store_ids=None,
    )
    indexed = IndexedProgramService(session).month_grid(
        tenant_id=faker_tenant["tenant_id"],
        month=month,
        perspective=perspective,
        manager_scope_store_ids=None,
    )

    assert indexed == legacy
