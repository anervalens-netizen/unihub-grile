"""PostgreSQL proof for durable worker lease semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ugrile.domain.enums import JobKind
from ugrile.domain.identifiers import make_tenant_id
from ugrile.repositories.models import OutboxJob, Tenant
from ugrile.worker.worker import claim_next, enqueue, recover_stale_jobs


def test_live_execution_lock_is_not_stolen_but_crashed_lease_is_recovered(
    pg_session_factory,
):
    """SKIP LOCKED protects a live handler; the same committed lease recovers after crash."""

    tenant_id = make_tenant_id("workerpg")
    now = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)

    seed = pg_session_factory()
    seed.add(Tenant(id=tenant_id, name="Worker PG", timezone="Europe/Bucharest"))
    seed.commit()
    queued = enqueue(
        seed,
        tenant_id=tenant_id,
        kind=JobKind.NOOP.value,
        idempotency_key="pg-worker-lease",
        run_after=now,
    )
    seed.commit()
    job_id = queued.id
    seed.close()

    worker_a = pg_session_factory()
    claimed = claim_next(
        worker_a,
        locked_by="worker-a",
        now=now,
        lease_seconds=60,
    )
    assert claimed is not None and claimed.id == job_id
    worker_a.commit()  # durable RUNNING lease

    # Simulate the live handler transaction: it locks its committed RUNNING row.
    live = worker_a.execute(
        select(OutboxJob).where(OutboxJob.id == job_id).with_for_update()
    ).scalar_one()
    assert live.status == "RUNNING"

    worker_b = pg_session_factory()
    requeued, failed = recover_stale_jobs(
        worker_b,
        now=now + timedelta(seconds=61),
        lease_seconds=60,
    )
    worker_b.commit()
    assert (requeued, failed) == (0, 0)
    worker_b.close()

    # Process crash/connection loss releases the execution lock while the
    # committed RUNNING state remains visible.
    worker_a.rollback()
    worker_a.close()

    worker_c = pg_session_factory()
    requeued, failed = recover_stale_jobs(
        worker_c,
        now=now + timedelta(seconds=61),
        lease_seconds=60,
    )
    worker_c.commit()
    assert (requeued, failed) == (1, 0)
    recovered = worker_c.get(OutboxJob, job_id)
    assert recovered is not None
    assert recovered.status == "PENDING"
    assert recovered.locked_by is None
    assert recovered.locked_at is None
    assert "LEASE_EXPIRED" in (recovered.last_error or "")
    worker_c.close()
