"""Sales attribution service.

Materialises :class:`SalesPersonDayProjection` rows in the same
transaction as the calendar CAS so a rolled-back calendar never leaves an
attribution row behind. The service is also the read side for the
``/months/{id}/attribution`` API.

Tenant safety
-------------

The service reads only the caller's tenant; the projection rows are
written with the same ``tenant_id``; cross-tenant FKs are guarded by the
composite foreign keys on ``sales_person_day_projections``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
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
    ) -> tuple[int, tuple[AttributedSale, ...], tuple[str, ...]]:
        """Recompute and persist the attribution projection for the revision.

        Must be called inside the calendar CAS transaction. Returns the
        number of rows written, the attributed sale rows (for tests/audit)
        and the connector generation(s) covered.
        """

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
        """Read the latest attribution revision for the month."""

        stmt = (
            select(SalesPersonDayProjection)
            .where(
                SalesPersonDayProjection.tenant_id == tenant_id,
                SalesPersonDayProjection.month_id == month.id,
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
        )

    def summary_for_revision(
        self, *, tenant_id: str, month: Month, revision: int
    ) -> AttributionSummary:
        rows = list_attribution(
            self.session,
            tenant_id=tenant_id,
            month_id=month.id,
            revision=revision,
        )
        company_total = Decimal("0")
        seen: set[tuple[str, object]] = set()
        anomalies: list[dict[str, object]] = []
        # Recompute company total deterministically by walking sales.
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
        company_total = result.company_total_amount
        for row in rows:
            seen.add((row.business_date.isoformat(), row.person_id))
        _ = seen  # reserved for future per-person totals
        return AttributionSummary(
            month_id=month.id,
            revision=revision,
            attributed=tuple(rows),
            total_rows=len(rows),
            company_total=company_total,
            anomalies=tuple(anomalies),
        )


__all__ = [
    "AttributionService",
    "AttributionSummary",
]
