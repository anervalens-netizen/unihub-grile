"""Central capability and resource-scope authorization boundary.

Roles answer *which operation* a principal may perform. Effective-dated scope
answers *which store resources* that operation may touch. API routes should use
this module instead of open-coding role and tenant checks.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from enum import StrEnum

from sqlalchemy.orm import Session

from ..domain.enums import RoleName
from ..domain.errors import ScopeError
from ..repositories.models import Month
from .auth import Principal, effective_store_ids


class Capability(StrEnum):
    CATALOG_READ = "catalog.read"
    SCHEDULE_READ = "schedule.read"
    SCHEDULE_WRITE = "schedule.write"
    GRID_READ = "grid.read"
    GRID_COMPUTE = "grid.compute"
    PAYROLL_MASTER_READ = "payroll.master.read"
    PAYROLL_MASTER_WRITE = "payroll.master.write"
    HOLIDAY_READ = "holiday.read"
    HOLIDAY_WRITE = "holiday.write"
    EPAY_READ = "epay.read"
    EPAY_WRITE = "epay.write"
    SHEET_READ = "sheet.read"
    SHEET_SYNC = "sheet.sync"
    SHEET_BIND = "sheet.bind"
    EXPORT_READ = "export.read"
    EXPORT_CREATE = "export.create"
    MONTH_READ = "month.read"
    MONTH_CLOSE_READ = "month.close.read"
    MONTH_CLOSE = "month.close"
    MONTH_REOPEN = "month.reopen"
    ADMIN_FIXTURE = "admin.fixture"
    JOBS_READ = "jobs.read"


_ROLE_CAPABILITIES: dict[RoleName, frozenset[Capability]] = {
    RoleName.ADMIN: frozenset(Capability),
    RoleName.MANAGER: frozenset(
        {
            Capability.CATALOG_READ,
            Capability.SCHEDULE_READ,
            Capability.SCHEDULE_WRITE,
            Capability.GRID_READ,
            Capability.HOLIDAY_READ,
            Capability.EPAY_READ,
            Capability.SHEET_READ,
            Capability.EXPORT_READ,
            Capability.MONTH_READ,
            Capability.JOBS_READ,
        }
    ),
    RoleName.READONLY: frozenset(
        {
            Capability.CATALOG_READ,
            Capability.HOLIDAY_READ,
            Capability.MONTH_READ,
        }
    ),
}


def capabilities_for(principal: Principal) -> frozenset[Capability]:
    """Return the finite capability set for the principal's application role."""

    return _ROLE_CAPABILITIES.get(principal.role, frozenset())


def authorize(principal: Principal, capability: Capability) -> None:
    """Require an operation capability independent of resource scope."""

    if capability not in capabilities_for(principal):
        raise ScopeError(
            "principal does not have the required capability",
            details={
                "principal": principal.user_id,
                "role": principal.role.value,
                "capability": capability.value,
            },
        )


def month_store_ids(
    session: Session,
    principal: Principal,
    month: Month,
) -> set[str]:
    """Return the union of stores visible to the principal during the month.

    Admin scope is tenant-wide and independent of effective date, so resolve it
    once. Managers can change scope mid-month and still require the union of all
    daily effective scopes.
    """

    if month.tenant_id != principal.tenant_id:
        raise ScopeError(
            "principal cannot access other tenants",
            details={
                "principal_tenant": principal.tenant_id,
                "requested_tenant": month.tenant_id,
            },
        )
    if principal.role is RoleName.ADMIN:
        return effective_store_ids(
            session,
            principal,
            date(month.year, month.month, 1),
        )

    days = monthrange(month.year, month.month)[1]
    visible: set[str] = set()
    for day_number in range(1, days + 1):
        visible.update(
            effective_store_ids(
                session,
                principal,
                date(month.year, month.month, day_number),
            )
        )
    return visible


def authorize_store_for_month(
    session: Session,
    principal: Principal,
    capability: Capability,
    *,
    month: Month,
    store_id: str,
) -> None:
    """Require capability plus visibility of one store during the month."""

    authorize(principal, capability)
    allowed = month_store_ids(session, principal, month)
    if store_id not in allowed:
        raise ScopeError(
            "requested store is outside principal scope",
            details={
                "principal": principal.user_id,
                "capability": capability.value,
                "store_id": store_id,
                "month_id": month.id,
            },
        )


def authorize_store_set_for_month(
    session: Session,
    principal: Principal,
    capability: Capability,
    *,
    month: Month,
    store_ids: set[str],
) -> None:
    """Require that every requested store is visible; never silently filter writes."""

    authorize(principal, capability)
    allowed = month_store_ids(session, principal, month)
    denied = sorted(store_ids - allowed)
    if denied:
        raise ScopeError(
            "one or more requested stores are outside principal scope",
            details={
                "principal": principal.user_id,
                "capability": capability.value,
                "month_id": month.id,
                "denied_store_ids": denied,
            },
        )


__all__ = [
    "Capability",
    "authorize",
    "authorize_store_for_month",
    "authorize_store_set_for_month",
    "capabilities_for",
    "month_store_ids",
]
