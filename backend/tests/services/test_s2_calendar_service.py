from datetime import date

import pytest

from ugrile.domain.enums import DayStatus, MonthState, RoleName, WorkingKind
from ugrile.domain.errors import ConflictError, ScopeError
from ugrile.repositories.models import ManagerScope, User
from ugrile.repositories.months import MonthRepository
from ugrile.services.auth import Principal, effective_store_ids
from ugrile.services.calendar import CalendarChange, CalendarService


def test_calendar_apply_is_revisioned_and_rejects_stale_and_closed(session, faker_tenant):
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    svc = CalendarService(session)
    result = svc.apply(
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
    assert result.revision == 1
    with pytest.raises(ConflictError):
        svc.apply(month=month, tenant_id=faker_tenant["tenant_id"], expected_revision=0, changes=[])
    month.state = MonthState.CLOSED.value
    with pytest.raises(ConflictError):
        svc.apply(month=month, tenant_id=faker_tenant["tenant_id"], expected_revision=1, changes=[])


def test_calendar_scope_rejects_outside_store_without_writes(session, faker_tenant):
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    svc = CalendarService(session)
    with pytest.raises(ScopeError):
        svc.apply(
            month=month,
            tenant_id=faker_tenant["tenant_id"],
            expected_revision=0,
            allowed_store_ids={faker_tenant["store_id"]},
            changes=[
                CalendarChange(
                    faker_tenant["person_c_id"],
                    date(2026, 8, 1),
                    faker_tenant["other_store_id"],
                    DayStatus.WORKING,
                    WorkingKind.NORMAL,
                )
            ],
        )
    assert month.revision == 0


def test_manager_scope_is_effective_dated_and_deny_by_default(session, faker_tenant):
    manager = User(
        id="user_manager",
        tenant_id=faker_tenant["tenant_id"],
        email="manager@acme.example",
        display_name="Manager",
        role=RoleName.MANAGER.value,
    )
    session.add(manager)
    session.add(
        ManagerScope(
            tenant_id=faker_tenant["tenant_id"],
            user_id=manager.id,
            store_id=faker_tenant["store_id"],
            effective_from=date(2026, 8, 1),
            effective_to=date(2026, 8, 15),
        )
    )
    session.commit()
    principal = Principal(manager.id, faker_tenant["tenant_id"], RoleName.MANAGER, manager.email)
    assert effective_store_ids(session, principal, date(2026, 8, 10)) == {faker_tenant["store_id"]}
    assert effective_store_ids(session, principal, date(2026, 8, 16)) == set()
