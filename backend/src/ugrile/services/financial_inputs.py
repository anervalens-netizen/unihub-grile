"""Current-authority validation for persisted grid financial inputs.

Final close must not trust a grid row merely because its calendar revision is
current. Salary master, incentives, targets and connector sales can change
without incrementing ``Month.revision``. This module compares the canonical
financial values persisted in a grid payload with the values that are
currently authoritative in the database and fails closed on any mismatch.
"""

from __future__ import annotations

import json
from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..repositories.models import (
    GridCalculation,
    IncentiveInput,
    Month,
    Person,
    SalesStoreDay,
    StoreTarget,
)
from ..repositories.salary import SalaryRepository


def _as_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _latest_incentive(
    session: Session, *, tenant_id: str, person_id: str, month: Month
) -> Decimal:
    row = session.execute(
        select(IncentiveInput)
        .where(
            IncentiveInput.tenant_id == tenant_id,
            IncentiveInput.person_id == person_id,
            IncentiveInput.year == month.year,
            IncentiveInput.month == month.month,
        )
        .order_by(IncentiveInput.version.desc(), IncentiveInput.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row.amount if row is not None else Decimal("0")


def _latest_target(
    session: Session, *, tenant_id: str, store_id: str, month: Month
) -> StoreTarget | None:
    return session.execute(
        select(StoreTarget)
        .where(
            StoreTarget.tenant_id == tenant_id,
            StoreTarget.store_id == store_id,
            StoreTarget.year == month.year,
            StoreTarget.month == month.month,
            StoreTarget.kind == "MONTHLY_SALES",
        )
        .order_by(StoreTarget.version.desc(), StoreTarget.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_sale(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    business_date: date,
) -> SalesStoreDay | None:
    return session.execute(
        select(SalesStoreDay)
        .where(
            SalesStoreDay.tenant_id == tenant_id,
            SalesStoreDay.store_id == store_id,
            SalesStoreDay.business_date == business_date,
        )
        .order_by(SalesStoreDay.generation.desc(), SalesStoreDay.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def financial_input_mismatch(
    session: Session,
    *,
    tenant_id: str,
    month: Month,
    person: Person,
    row: GridCalculation,
) -> str | None:
    """Return the first stale canonical financial fact, else ``None``.

    The comparison intentionally ignores E-pay because ``PolicyCloseService``
    validates the month-bound E-pay snapshot separately. Calendar/Pontaj
    changes are already protected by the exact ``Month.revision`` requirement.
    """

    try:
        payload = json.loads(row.payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "grid payload is not valid JSON"
    if not isinstance(payload, dict):
        return "grid payload is not an object"
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        return "grid payload has no canonical inputs"
    parameters = inputs.get("parameters")
    if not isinstance(parameters, dict):
        return "grid payload has no canonical parameters"

    first_day = date(month.year, month.month, 1)
    salary, tickets, flip = SalaryRepository(session).find_effective_window(
        tenant_id=tenant_id,
        person_id=person.id,
        on_date=first_day,
    )
    salary = salary if salary is not None else Decimal("0")
    tickets = tickets if tickets is not None else Decimal("0")
    flip = flip if flip is not None else Decimal("0")
    incentive = _latest_incentive(
        session,
        tenant_id=tenant_id,
        person_id=person.id,
        month=month,
    )
    expected_parameters = {
        "salary": salary,
        "tickets": tickets,
        "flip": flip,
        "incentive": incentive,
    }
    for key, expected in expected_parameters.items():
        actual = _as_decimal(parameters.get(key))
        if actual != expected:
            return f"{key} changed: grid={actual}, current={expected}"

    calendar = inputs.get("calendar")
    if not isinstance(calendar, list):
        return "grid payload has no canonical calendar"
    days_in_month = monthrange(month.year, month.month)[1]
    for entry in calendar:
        if not isinstance(entry, dict):
            return "grid calendar entry is not an object"
        store_id = entry.get("store_id")
        raw_date = entry.get("business_date")
        if not isinstance(store_id, str) or not isinstance(raw_date, str):
            return "grid calendar entry has invalid store/date"
        try:
            business_date = date.fromisoformat(raw_date)
        except ValueError:
            return f"grid calendar date is invalid: {raw_date}"

        sale = _latest_sale(
            session,
            tenant_id=tenant_id,
            store_id=store_id,
            business_date=business_date,
        )
        expected_sales = sale.amount if sale is not None else Decimal("0")
        expected_sim = sale.sim_quantity if sale is not None else 0
        actual_sales = _as_decimal(entry.get("sales_amount"))
        actual_sim = entry.get("sim_quantity")
        if actual_sales != expected_sales:
            return (
                f"sales changed for {store_id}/{business_date.isoformat()}: "
                f"grid={actual_sales}, current={expected_sales}"
            )
        if not isinstance(actual_sim, int) or actual_sim != expected_sim:
            return (
                f"SIM quantity changed for {store_id}/{business_date.isoformat()}: "
                f"grid={actual_sim}, current={expected_sim}"
            )

        target = _latest_target(
            session,
            tenant_id=tenant_id,
            store_id=store_id,
            month=month,
        )
        if target is None:
            expected_target = Decimal("0")
        else:
            divisor = target.sales_days if target.sales_days is not None else days_in_month
            expected_target = (target.amount / Decimal(divisor)).quantize(Decimal("0.01"))
        actual_target = _as_decimal(entry.get("target_amount"))
        if actual_target != expected_target:
            return (
                f"target changed for {store_id}/{business_date.isoformat()}: "
                f"grid={actual_target}, current={expected_target}"
            )

    return None


__all__ = ["financial_input_mismatch"]
