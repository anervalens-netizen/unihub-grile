"""Current-authority validation for persisted grid financial inputs.

Final close must not trust a grid row merely because its calendar revision is
current. Salary master, incentives, targets and connector sales can change
without incrementing ``Month.revision``. A persisted payload can also be
partial or corrupted while retaining a current-looking revision. This module
therefore checks current authoritative values and connector generation,
validates the payload hashes, and reconstructs the complete current canonical
grid before close.
"""

from __future__ import annotations

import json
from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.rule_pack import hash_inputs
from ..repositories.models import (
    GridCalculation,
    IncentiveInput,
    Month,
    Person,
    SalesStoreDay,
    StoreTarget,
)
from ..repositories.salary import SalaryRepository
from .grid import GridService


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


def _latest_sales_generation(
    session: Session, *, tenant_id: str, month: Month
) -> str | None:
    """Return the newest persisted generation discriminator for the month.

    The current connector contract uses stable sortable generation strings.
    Historical generations remain stored for reconciliation, but final close
    must bind to the newest accepted generation instead of merely comparing
    the monetary values from an older snapshot.
    """

    first = date(month.year, month.month, 1)
    last_day = monthrange(month.year, month.month)[1]
    last = date(month.year, month.month, last_day)
    return session.execute(
        select(SalesStoreDay.generation)
        .where(
            SalesStoreDay.tenant_id == tenant_id,
            SalesStoreDay.business_date >= first,
            SalesStoreDay.business_date <= last,
        )
        .order_by(SalesStoreDay.generation.desc(), SalesStoreDay.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _sale_for_generation(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    business_date: date,
    generation: str,
) -> SalesStoreDay | None:
    return session.execute(
        select(SalesStoreDay)
        .where(
            SalesStoreDay.tenant_id == tenant_id,
            SalesStoreDay.store_id == store_id,
            SalesStoreDay.business_date == business_date,
            SalesStoreDay.generation == generation,
        )
        .order_by(SalesStoreDay.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _payload_integrity_mismatch(
    payload: dict[str, object], row: GridCalculation
) -> str | None:
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        return "grid payload has no canonical inputs"
    components = payload.get("components")
    if not isinstance(components, dict):
        return "grid payload has no canonical components"
    anomalies = payload.get("anomalies")
    if not isinstance(anomalies, list):
        return "grid payload has no canonical anomalies"
    if hash_inputs(inputs) != row.inputs_hash:
        return "persisted inputs_hash does not match the grid payload"
    if hash_inputs(components) != row.outputs_hash:
        return "persisted outputs_hash does not match the grid payload"
    return None


def _current_snapshot_mismatch(
    session: Session,
    *,
    tenant_id: str,
    month: Month,
    person: Person,
    row: GridCalculation,
    payload: dict[str, object],
) -> str | None:
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        return "grid payload has no canonical inputs"
    sales_generation = inputs.get("sales_generation")
    if not isinstance(sales_generation, str) or not sales_generation:
        return "grid payload has no sales generation discriminator"

    expected = GridService(session).compute_for_person(
        tenant_id=tenant_id,
        month=month,
        person_id=person.id,
        sales_generation=sales_generation,
    )
    if expected.inputs_hash != row.inputs_hash:
        return (
            "complete current canonical input hash differs from the persisted grid: "
            f"grid={row.inputs_hash}, current={expected.inputs_hash}"
        )
    if expected.outputs_hash != row.outputs_hash:
        return (
            "complete current canonical output hash differs from the persisted grid: "
            f"grid={row.outputs_hash}, current={expected.outputs_hash}"
        )

    persisted_anomalies = payload.get("anomalies")
    expected_anomalies = list(expected.anomalies)
    if persisted_anomalies != expected_anomalies:
        return "persisted grid anomalies differ from the complete current calculation"
    return None


def financial_input_mismatch(
    session: Session,
    *,
    tenant_id: str,
    month: Month,
    person: Person,
    row: GridCalculation,
) -> str | None:
    """Return the first stale/corrupt canonical financial fact, else ``None``."""

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

    persisted_generation = inputs.get("sales_generation")
    if not isinstance(persisted_generation, str) or not persisted_generation:
        return "grid payload has no sales generation discriminator"
    current_generation = _latest_sales_generation(
        session,
        tenant_id=tenant_id,
        month=month,
    )
    if current_generation is not None and persisted_generation != current_generation:
        return (
            "sales changed because generation changed: "
            f"grid={persisted_generation}, current={current_generation}"
        )
    accepted_generation = current_generation or persisted_generation

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

        sale = _sale_for_generation(
            session,
            tenant_id=tenant_id,
            store_id=store_id,
            business_date=business_date,
            generation=accepted_generation,
        )
        expected_sales = sale.amount if sale is not None else Decimal("0")
        expected_sim = sale.sim_quantity if sale is not None else 0
        actual_sales = _as_decimal(entry.get("sales_amount"))
        actual_sim = entry.get("sim_quantity")
        if actual_sales != expected_sales:
            return (
                f"sales changed for {store_id}/{business_date.isoformat()} "
                f"in generation {accepted_generation}: "
                f"grid={actual_sales}, current={expected_sales}"
            )
        if not isinstance(actual_sim, int) or actual_sim != expected_sim:
            return (
                f"SIM quantity changed for {store_id}/{business_date.isoformat()} "
                f"in generation {accepted_generation}: "
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

    integrity_mismatch = _payload_integrity_mismatch(payload, row)
    if integrity_mismatch is not None:
        return integrity_mismatch
    return _current_snapshot_mismatch(
        session,
        tenant_id=tenant_id,
        month=month,
        person=person,
        row=row,
        payload=payload,
    )


__all__ = ["financial_input_mismatch"]
