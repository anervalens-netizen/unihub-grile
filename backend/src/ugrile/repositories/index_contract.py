"""Operational secondary indexes justified by current PostgreSQL access paths.

The ORM model module owns entity/constraint definitions. This module owns the
small set of non-unique secondary indexes added after the M3 query-profile
review. Importing it attaches the indexes to ``Base.metadata`` so Alembic drift
checks and disposable PostgreSQL metadata tests see the same contract.

Keep this list query-driven: an index belongs here only while a named production
read/worker path needs its leading columns or predicate.
"""

from __future__ import annotations

from sqlalchemy import Index, text

from .models import EpayObservation, GridCalculation, OutboxJob, SalesStoreDay, SheetProjectionRun

INDEX_CONTRACT = (
    # worker.claim_next(): due PENDING work ordered by run_after then id.
    Index(
        "ix_outbox_pending_due",
        OutboxJob.run_after,
        OutboxJob.id,
        postgresql_where=text("status = 'PENDING'"),
        sqlite_where=text("status = 'PENDING'"),
    ),
    # worker.recover_stale_jobs(): expired RUNNING leases by locked_at.
    Index(
        "ix_outbox_running_lease",
        OutboxJob.locked_at,
        OutboxJob.id,
        postgresql_where=text("status = 'RUNNING'"),
        sqlite_where=text("status = 'RUNNING'"),
    ),
    # /worker/jobs/diagnostics: tenant + queue/terminal state + newest-first id.
    Index(
        "ix_outbox_tenant_status_id",
        OutboxJob.tenant_id,
        OutboxJob.status,
        OutboxJob.id,
    ),
    # GET /months/{id}/grid and grid replacement for one rule-pack revision.
    Index(
        "ix_grid_calculations_current_read",
        GridCalculation.tenant_id,
        GridCalculation.month_id,
        GridCalculation.rule_pack_version,
        GridCalculation.revision,
        GridCalculation.store_id,
        GridCalculation.person_id,
    ),
    # repositories.epay.latest_snapshot(): exact person/store/month source,
    # newest valid observations first. Invalid forensic rows are excluded.
    Index(
        "ix_epay_observations_latest_valid",
        EpayObservation.tenant_id,
        EpayObservation.store_id,
        EpayObservation.person_id,
        EpayObservation.source,
        EpayObservation.observed_at,
        EpayObservation.id,
        postgresql_where=text("is_valid IS true"),
        sqlite_where=text("is_valid = 1"),
    ),
    # attribution.store_sales_for_month(): tenant + business-date range.
    Index(
        "ix_sales_store_day_tenant_date",
        SalesStoreDay.tenant_id,
        SalesStoreDay.business_date,
        SalesStoreDay.store_id,
        SalesStoreDay.generation,
    ),
    # fake/live Google readback seam: newest run for one tenant/store.
    Index(
        "ix_sheet_projection_runs_store_latest",
        SheetProjectionRun.tenant_id,
        SheetProjectionRun.store_id,
        SheetProjectionRun.id,
    ),
)


__all__ = ["INDEX_CONTRACT"]
