from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ugrile.domain.enums import JobKind
from ugrile.repositories.models import OutboxJob
from ugrile.worker import worker as worker_module
from ugrile.worker.policy import retry_policy_for
from ugrile.worker.worker import enqueue, run_once


def test_google_provider_timeout_retries_with_backoff_then_exhausts(
    monkeypatch, session, faker_tenant
):
    """A Google-provider timeout follows its bounded retry contract (JOB-011)."""

    def timeout_handler(_session, _tenant_id, _payload):
        raise TimeoutError("provider request timed out")

    monkeypatch.setattr(worker_module, "get_handler", lambda _kind: timeout_handler)
    queued = enqueue(
        session,
        tenant_id=faker_tenant["tenant_id"],
        kind=JobKind.GOOGLE_PROJECTION_STORE.value,
        idempotency_key="timeout-provider-contract",
    )
    session.commit()
    policy = retry_policy_for(JobKind.GOOGLE_PROJECTION_STORE.value)

    for attempt in range(1, policy.max_attempts + 1):
        current = session.get(OutboxJob, queued.id)
        assert current is not None
        current.run_after = datetime.now(tz=UTC) - timedelta(seconds=1)
        session.commit()

        row, result = run_once(locked_by="timeout-worker")
        assert row is not None
        assert result is None
        assert row.attempts == attempt
        assert "TimeoutError" in (row.last_error or "")

        if attempt < policy.max_attempts:
            assert row.status == "PENDING"
            assert row.locked_by is None
            assert row.locked_at is None
            assert "RETRYABLE" in (row.last_error or "")
        else:
            assert row.status == "FAILED"
            assert row.locked_by is None
            assert row.locked_at is None
            assert "RETRY_EXHAUSTED" in (row.last_error or "")
