"""Import one read-only Retail reporting snapshot into Grile."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence

from sqlalchemy import func, select

from ..connectors.retail_ingest import RetailSnapshotIngestService
from ..connectors.retail_postgres import PostgresRetailSnapshotAdapter
from ..core.database import session_scope
from ..domain.enums import DayStatus, MonthState, RoleName, WorkingKind
from ..repositories.models import Person, SiteDayAssignment, Store, User
from ..repositories.months import MonthRepository
from ..services.attribution import AttributionService
from ..services.payroll_grid import PayrollGridService


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", required=True)
    parser.add_argument("--tenant-id", default="tenant_mobiup")
    args = parser.parse_args(argv)
    retail_url = os.environ.get("UGRILE_RETAIL_DATABASE_URL", "").strip()
    if not retail_url:
        raise SystemExit("UGRILE_RETAIL_DATABASE_URL is required")

    adapter = PostgresRetailSnapshotAdapter(retail_url)
    with session_scope() as session:
        result = RetailSnapshotIngestService(session).apply_adapter(
            adapter,
            tenant_id=args.tenant_id,
            period=args.period,
        )
        year, month_number = (int(part) for part in args.period.split("-", 1))
        month = MonthRepository(session).get_or_create(args.tenant_id, year, month_number)
        if month.state == MonthState.DRAFT.value:
            month.state = MonthState.OPEN.value
        existing_calendar_rows = session.scalar(
            select(func.count()).select_from(SiteDayAssignment).where(
                SiteDayAssignment.tenant_id == args.tenant_id,
                SiteDayAssignment.month_id == month.id,
            )
        )
        seeded_assignments = 0
        if not existing_calendar_rows:
            stores_by_external = {
                row.external_code: row.id
                for row in session.execute(
                    select(Store).where(Store.tenant_id == args.tenant_id)
                ).scalars()
                if row.external_code
            }
            people_by_external = {
                row.external_code: row.id
                for row in session.execute(
                    select(Person).where(Person.tenant_id == args.tenant_id)
                ).scalars()
                if row.external_code
            }
            for external_store, business_date, external_person in adapter.observed_assignments:
                store_id = stores_by_external.get(external_store)
                person_id = people_by_external.get(external_person)
                if store_id is None or person_id is None:
                    continue
                session.add(
                    SiteDayAssignment(
                        tenant_id=args.tenant_id,
                        month_id=month.id,
                        store_id=store_id,
                        person_id=person_id,
                        business_date=business_date,
                        status=DayStatus.WORKING.value,
                        working_kind=WorkingKind.NORMAL.value,
                        revision=1,
                        source="RETAIL_ACTIVITY_SEED",
                    )
                )
                seeded_assignments += 1
            if seeded_assignments:
                month.revision = 1
                session.flush()
                AttributionService(session).rebuild_for_month(
                    month=month,
                    tenant_id=args.tenant_id,
                    revision=month.revision,
                )
                PayrollGridService(session).compute_and_persist(
                    tenant_id=args.tenant_id,
                    month=month,
                )
        admin_id = "user_mobiup_admin"
        if session.get(User, admin_id) is None:
            session.add(
                User(
                    id=admin_id,
                    tenant_id=args.tenant_id,
                    email="admin@grile.local",
                    display_name="Administrator Grile",
                    is_active=True,
                    role=RoleName.ADMIN.value,
                )
            )
        store_count = len(
            list(
                session.execute(
                    select(Store.id).where(Store.tenant_id == args.tenant_id)
                ).scalars()
            )
        )
        result = {
            **result,
            "month_id": month.id,
            "stores_total": store_count,
            "seeded_assignments": seeded_assignments,
        }
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
