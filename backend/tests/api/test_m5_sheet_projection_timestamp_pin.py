"""GS-006 durable projection identity proof for projected_at."""

from __future__ import annotations

import json

from ugrile.core import database
from ugrile.repositories.models import OutboxJob, SheetProjectionRun
from ugrile.repositories.months import MonthRepository
from ugrile.worker.worker import run_once

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}


def test_projection_timestamp_is_pinned_from_enqueue_through_worker_retry_identity(
    client,
    faker_tenant,
) -> None:
    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(
            faker_tenant["tenant_id"],
            2026,
            8,
        )
        month_id = month.id

    response = client.post(
        f"/months/{month_id}/sheet-projection/enqueue",
        headers=ADMIN,
        json={"store_id": faker_tenant["store_id"]},
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]

    with database.session_scope() as session:
        job = session.get(OutboxJob, job_id)
        assert job is not None
        queued_payload = json.loads(job.payload)
        projected_at = queued_payload["projected_at"]
        assert isinstance(projected_at, str)
        assert projected_at

    row, result = run_once(locked_by="gs006-timestamp-test-worker")
    assert row is not None
    assert row.id == job_id
    assert row.status == "DONE"
    assert result is not None

    with database.session_scope() as session:
        run = (
            session.query(SheetProjectionRun)
            .filter_by(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["store_id"],
                status="DONE",
            )
            .order_by(SheetProjectionRun.id.desc())
            .one()
        )
        persisted = json.loads(run.payload)
        assert persisted["metadata"]["projected_at"] == projected_at
        assert persisted["grila"]["generated_at"] == projected_at
        assert persisted["reconciliation"]["projected_at"] == projected_at
