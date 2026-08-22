"""Resource scoping for daily attribution reads.

Attribution rows are store/person/date facts. Manager visibility therefore
uses effective store scope and effective person home-store history for the
specific business date; a month-wide union is not sufficient.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from ..domain.enums import RoleName
from ..repositories.models import Month, SalesPersonDayProjection
from .auth import Principal, effective_store_ids
from .person_scope import effective_home_store_map


def _anomaly_date(anomaly: dict[str, object]) -> date | None:
    raw = anomaly.get("business_date")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def scope_attribution(
    session: Session,
    *,
    principal: Principal,
    month: Month,
    rows: tuple[SalesPersonDayProjection, ...],
    anomalies: tuple[dict[str, object], ...],
    requested_store_id: str | None,
) -> tuple[list[SalesPersonDayProjection], list[dict[str, object]]]:
    """Return only attribution facts whose complete resource tuple is visible."""

    if principal.role is RoleName.ADMIN:
        visible_rows = [
            row
            for row in rows
            if requested_store_id is None or row.store_id == requested_store_id
        ]
        visible_anomalies = [
            dict(anomaly)
            for anomaly in anomalies
            if requested_store_id is None
            or str(anomaly.get("store_id") or "") == requested_store_id
        ]
        return visible_rows, visible_anomalies

    person_dates: set[tuple[str, date]] = {
        (row.person_id, row.business_date) for row in rows
    }
    for anomaly in anomalies:
        anomaly_person = anomaly.get("person_id")
        business_date = _anomaly_date(anomaly)
        if isinstance(anomaly_person, str) and anomaly_person and business_date is not None:
            person_dates.add((anomaly_person, business_date))

    effective_home = effective_home_store_map(
        session,
        tenant_id=principal.tenant_id,
        person_ids={person_id for person_id, _ in person_dates},
        business_dates={business_date for _, business_date in person_dates},
    )
    allowed_cache: dict[date, set[str]] = {}

    def allowed_on(business_date: date) -> set[str]:
        allowed = allowed_cache.get(business_date)
        if allowed is None:
            allowed = effective_store_ids(session, principal, business_date)
            allowed_cache[business_date] = allowed
        return allowed

    visible_rows: list[SalesPersonDayProjection] = []
    for row in rows:
        if requested_store_id is not None and row.store_id != requested_store_id:
            continue
        allowed = allowed_on(row.business_date)
        if row.store_id not in allowed:
            continue
        if effective_home.get((row.person_id, row.business_date)) not in allowed:
            continue
        visible_rows.append(row)

    visible_anomalies: list[dict[str, object]] = []
    for anomaly in anomalies:
        anomaly_store = anomaly.get("store_id")
        business_date = _anomaly_date(anomaly)
        if not isinstance(anomaly_store, str) or not anomaly_store or business_date is None:
            continue
        if requested_store_id is not None and anomaly_store != requested_store_id:
            continue
        allowed = allowed_on(business_date)
        if anomaly_store not in allowed:
            continue
        anomaly_person = anomaly.get("person_id")
        if (
            isinstance(anomaly_person, str)
            and anomaly_person
            and effective_home.get((anomaly_person, business_date)) not in allowed
        ):
            continue
        visible_anomalies.append(dict(anomaly))

    return visible_rows, visible_anomalies


__all__ = ["scope_attribution"]
