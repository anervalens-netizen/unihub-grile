"""Sales attribution service.

Materialises :class:`SalesPersonDayProjection` rows from calendar + physical
store/day sales. Calendar CAS and Retail accepted-generation refreshes both use
the same projection builder; rolled-back callers leave no projection evidence.
The service is also the read side for ``/months/{id}/attribution``.

Tenant/generation safety
------------------------

The service reads only the caller's tenant. For a Retail-backed period the read
side returns only projection rows whose generation equals the accepted Retail
head; historical generation rows remain stored but cannot masquerade as current.
Accepted-head filtering is embedded in the existing SQL statements so it does
not consume another calibrated M3 SELECT round-trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import literal, or_, select
from sqlalchemy.orm import Session

from ..domain.attribution import AttributedSale, AttributionResult, attribute_sales
from ..domain.errors import ValidationError
from ..repositories.attribution import (
    attribute_for_month,
    list_attribution,
    persist_attribution,
    store_sales_for_month,
    working_days_for_month,
)
from ..repositories.models import (
    Month,
    SalesPersonDayProjection,
)
from ..repositories.retail_generation import (
    accepted_retail_generation_summary_subquery,
)


@dataclass(frozen=True, slots=True)
class AttributionSummary:
    month_id: str
    revision: int
    attributed: tuple[SalesPersonDayProjection, ...]
    total_rows: int
    company_total: Decimal
    anomalies: tuple[dict[str, object], ...]


class AttributionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def rebuild_for_month(
        self,
        *,
        month: Month,
        tenant_id: str,
        revision: int,
        generation: str | None = None,
        resolve_accepted_generation: bool = True,
    ) -> tuple[int, tuple[AttributedSale, ...], tuple[str, ...]]:
        """Recompute and persist attribution for one calendar revision."""

        if month.tenant_id != tenant_id:
            raise ValidationError(
                "month belongs to another tenant",
                details={"month_id": month.id},
            )
        attributed, generations = attribute_for_month(
            self.session,
            tenant_id=tenant_id,
            month_id=month.id,
            year=month.year,
            month=month.month,
            generation=generation,
            resolve_accepted_generation=resolve_accepted_generation,
        )
        count = persist_attribution(
            self.session,
            tenant_id=tenant_id,
            month_id=month.id,
            revision=revision,
            attributed=attributed,
        )
        return count, tuple(attributed), generations

    def latest_attribution(
        self, *, tenant_id: str, month: Month
    ) -> list[SalesPersonDayProjection]:
        """Read the latest projection revision, pinned to accepted Retail head."""

        period = f"{month.year:04d}-{month.month:02d}"
        accepted_summary = accepted_retail_generation_summary_subquery(
            tenant_id=tenant_id,
            period=period,
        )
        generation_marker = (
            literal('%"generation_key":"')
            + SalesPersonDayProjection.generation
            + literal('"%')
        )
        stmt = (
            select(SalesPersonDayProjection)
            .where(
                SalesPersonDayProjection.tenant_id == tenant_id,
                SalesPersonDayProjection.month_id == month.id,
                or_(
                    accepted_summary.is_(None),
                    accepted_summary.like(generation_marker),
                ),
            )
            .order_by(SalesPersonDayProjection.revision.desc())
            .limit(1)
        )
        latest = self.session.execute(stmt).scalar_one_or_none()
        if latest is None:
            return []
        return list_attribution(
            self.session,
            tenant_id=tenant_id,
            month_id=month.id,
            revision=latest.revision,
            period=period,
            resolve_accepted_generation=True,
        )

    def summary_for_revision(
        self, *, tenant_id: str, month: Month, revision: int
    ) -> AttributionSummary:
        period = f"{month.year:04d}-{month.month:02d}"
        rows = list_attribution(
            self.session,
            tenant_id=tenant_id,
            month_id=month.id,
            revision=revision,
            period=period,
            resolve_accepted_generation=True,
        )
        anomalies: list[dict[str, object]] = []
        sales = store_sales_for_month(
            self.session,
            tenant_id=tenant_id,
            year=month.year,
            month=month.month,
        )
        working = working_days_for_month(
            self.session, tenant_id=tenant_id, month_id=month.id
        )
        result: AttributionResult = attribute_sales(working, sales)
        for anomaly in result.anomalies:
            anomalies.append(
                {
                    "code": anomaly.code,
                    "store_id": anomaly.store_id,
                    "person_id": anomaly.person_id,
                    "business_date": anomaly.business_date.isoformat()
                    if anomaly.business_date
                    else None,
                    "message": anomaly.message,
                }
            )
        return AttributionSummary(
            month_id=month.id,
            revision=revision,
            attributed=tuple(rows),
            total_rows=len(rows),
            company_total=result.company_total_amount,
            anomalies=tuple(anomalies),
        )


__all__ = [
    "AttributionService",
    "AttributionSummary",
]
