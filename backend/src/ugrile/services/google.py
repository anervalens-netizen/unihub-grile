"""S5a Google projection service.

This module orchestrates the fake Google adapter for the
``GOOGLE_PROJECTION_STORE`` job. The service is invoked **only** by the
durable worker; the API layer enqueues, the worker executes (see
``ugrile.worker.jobs._job_google_projection_store``).

Projection contract
-------------------

* Input: a ``month_id`` + ``store_id`` pair plus the calendar revision.
* The service materialises the Grila + Pontaj lattice for the store from
  the canonical tables and hands the deterministic dict to the fake
  adapter.
* The adapter writes the projection into ``sheet_projection_runs`` +
  ``sheet_bindings`` and returns the ``StoreProjection`` value object.
* On provider failure (``UGR_S5_GOOGLE_FAIL=1``), the adapter raises
  ``GoogleAdapterError``; the service re-raises it after the worker has
  marked the job row ``FAILED``. The last-good projection remains
  untouched.

Tenant safety
-------------

Every read scopes by ``tenant_id`` first; the projection never crosses
tenant boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors.google import (
    GoogleAdapterError,
    StoreProjection,
    write_store_projection,
)
from ..domain.enums import ConnectorGeneration, DayStatus
from ..repositories.models import (
    GridCalculation,
    PontajProjection,
    SalesPersonDayProjection,
    SiteDayAssignment,
    Store,
    StoreTarget,
)


@dataclass(frozen=True, slots=True)
class ProjectionPayload:
    """Structural projection payload ready for the adapter."""

    store_id: str
    generation: str
    grila: dict[str, Any]
    pontaj: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProjectionOutcome:
    """Outcome of one projection run."""

    store_id: str
    generation: str
    projection: StoreProjection


def _latest_store_target(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    year: int,
    month: int,
) -> StoreTarget | None:
    rows = list(
        session.execute(
            select(StoreTarget).where(
                StoreTarget.tenant_id == tenant_id,
                StoreTarget.store_id == store_id,
                StoreTarget.year == year,
                StoreTarget.month == month,
            )
        ).scalars()
    )
    latest: StoreTarget | None = None
    for row in rows:
        if latest is None or row.version > latest.version:
            latest = row
    return latest


def _build_payload(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    month_id: str,
    revision: int,
    generation: str,
) -> ProjectionPayload:
    """Build the deterministic structural projection for one store/month."""

    assignments = list(
        session.execute(
            select(SiteDayAssignment).where(
                SiteDayAssignment.tenant_id == tenant_id,
                SiteDayAssignment.month_id == month_id,
                SiteDayAssignment.store_id == store_id,
                SiteDayAssignment.status == DayStatus.WORKING.value,
            )
        ).scalars()
    )
    pontaj = list(
        session.execute(
            select(PontajProjection).where(
                PontajProjection.tenant_id == tenant_id,
                PontajProjection.month_id == month_id,
                PontajProjection.revision == revision,
                PontajProjection.person_id.in_({a.person_id for a in assignments}),
            )
        ).scalars()
    )

    grila_rows: list[dict[str, Any]] = []
    for assignment in sorted(assignments, key=lambda a: (a.business_date, a.person_id)):
        grila_rows.append(
            {
                "business_date": assignment.business_date.isoformat(),
                "person_id": assignment.person_id,
                "status": assignment.status,
                "working_kind": assignment.working_kind,
                "revision": assignment.revision,
            }
        )
    pontaj_rows: list[dict[str, Any]] = []
    for row in sorted(pontaj, key=lambda r: (r.person_id, r.business_date)):
        pontaj_rows.append(
            {
                "person_id": row.person_id,
                "business_date": row.business_date.isoformat(),
                "status": row.status,
                "start_time": row.start_time.isoformat() if row.start_time else None,
                "end_time": row.end_time.isoformat() if row.end_time else None,
                "pause_minutes": int(row.pause_minutes),
                "hours": str(row.hours),
            }
        )
    return ProjectionPayload(
        store_id=store_id,
        generation=generation,
        grila={"revision": revision, "rows": grila_rows},
        pontaj={"revision": revision, "rows": pontaj_rows},
    )


class GoogleProjectionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def project_store_for_month(
        self,
        *,
        tenant_id: str,
        store_id: str,
        month_id: str,
        year: int,
        month: int,
        revision: int,
        generation: str | None = None,
    ) -> ProjectionOutcome:
        """Build and write the projection for one store/month.

        The function validates the (tenant, store) tuple up-front, then
        delegates to the fake adapter. The adapter is the sole writer;
        this service does not touch ``sheet_projection_runs`` directly.
        """

        store = self.session.execute(
            select(Store).where(Store.tenant_id == tenant_id, Store.id == store_id)
        ).scalar_one_or_none()
        if store is None:
            raise GoogleAdapterError(
                "store not found for projection",
                details={"tenant_id": tenant_id, "store_id": store_id},
            )
        # Read-only target lookup so the payload carries the target
        # fingerprint even when the calendar has no WORKING rows yet.
        target = _latest_store_target(
            self.session,
            tenant_id=tenant_id,
            store_id=store_id,
            year=year,
            month=month,
        )
        gen = generation or ConnectorGeneration.FIXTURE_V1.value
        payload = _build_payload(
            self.session,
            tenant_id=tenant_id,
            store_id=store_id,
            month_id=month_id,
            revision=revision,
            generation=gen,
        )
        # Embed the target fingerprint in the structural payload so
        # structural readback can prove the projection respects the
        # versioned target input.
        payload_dict: dict[str, Any] = {
            "grila": {
                **payload.grila,
                "target": {
                    "amount": str(target.amount) if target is not None else None,
                    "currency": target.currency if target is not None else None,
                    "version": target.version if target is not None else None,
                    "sales_days": target.sales_days if target is not None else None,
                },
                "generated_at": datetime.now(tz=UTC).isoformat(),
            },
            "pontaj": dict(payload.pontaj),
        }
        projection = write_store_projection(
            self.session,
            tenant_id=tenant_id,
            store_id=store_id,
            generation=gen,
            payload=payload_dict,
        )
        return ProjectionOutcome(
            store_id=store_id,
            generation=gen,
            projection=projection,
        )


def grid_revision_for_month(
    session: Session,
    *,
    tenant_id: str,
    month_id: str,
) -> int | None:
    """Return the latest persisted grid revision for the month, if any."""

    return session.execute(
        select(GridCalculation.revision)
        .where(
            GridCalculation.tenant_id == tenant_id,
            GridCalculation.month_id == month_id,
        )
        .order_by(GridCalculation.revision.desc())
        .limit(1)
    ).scalar_one_or_none()


def attribution_count_for_store(
    session: Session,
    *,
    tenant_id: str,
    month_id: str,
    store_id: str,
) -> int:
    """Count attributed projection rows for one store/month (diagnostic)."""

    return int(
        session.execute(
            select(SalesPersonDayProjection).where(
                SalesPersonDayProjection.tenant_id == tenant_id,
                SalesPersonDayProjection.month_id == month_id,
                SalesPersonDayProjection.store_id == store_id,
            )
        )
        .scalars()
        .all()
        .__len__()
    )


__all__ = [
    "GoogleProjectionService",
    "ProjectionOutcome",
    "ProjectionPayload",
    "attribution_count_for_store",
    "grid_revision_for_month",
]
