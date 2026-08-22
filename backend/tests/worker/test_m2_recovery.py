from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ugrile.domain.enums import JobKind
from ugrile.domain.errors import DomainError
from ugrile.repositories.models import OutboxJob
from ugrile.worker import worker as worker_module
from ugrile.worker.policy import retry_policy_for
from ugrile.worker.worker import claim_next, enqueue, run_once


def _now() -> datetime:
    return datetime(2026, 8, 22, 10, 0, tzinfo=UTC)


def test_claim_respects_run_after_and_prefers_due_job(session, faker_tenant):
    now = _now()
    future = enqueue(
        session,
        tenant_id=faker_tenant["tenant_id"],
        kind=JobKind.NOOP.value,
        idempotency_key="future-job",
        run_after=now + timedelta(hours=1),
    )
    due = enqueue(
        session,
        tenant_id=faker_tenant["tenant_id"],
        kind=JobKind.NOOP.value,
        idempotency_key="due-job",
        run_after=now - timedelta(seconds=1),
    )
    session.commit()

    claimed = claim_next(session, locked_by="worker-a", now=now)
    assert claimed is not None
    assert claimed.id == due.id
    assert claimed.status == "RUNNING"
    assert claimed.attempts == 1
    assert future.status == "PENDING"


def test_committed_crash_claim_is_recovered_after_lease(session, faker_tenant):
    now = _now()
    row = enqueue(
        session,
        tenant_id=faker_tenant["tenant_id"],
        kind=JobKind.NOOP.value,
        idempotency_key="crash-recovery",
        run_after=now,
    )
    session.commit()

    first = claim_next(session, locked_by="dead-worker", now=now, lease_seconds=60)
    assert first is not None
    assert first.id == row.id
    assert first.status == "RUNNING"
    assert first.attempts == 1
    session.commit()  # simulate the committed lease before process death

    reclaimed = claim_next(
        session,
        locked_by="replacement-worker",
        now=now + timedelta(seconds=61),
        lease_seconds=60,
    )
    assert reclaimed is not None
    assert reclaimed.id == row.id
    assert reclaimed.status == "RUNNING"
    assert reclaimed.attempts == 2
    assert reclaimed.locked_by == "replacement-worker"


def test_stale_job_at_attempt_limit_becomes_failed(session, faker_tenant):
    now = _now()
    stale = enqueue(
        session,
        tenant_id=faker_tenant["tenant_id"],
        kind=JobKind.NOOP.value,
        idempotency_key="stale-exhausted",
        run_after=now,
    )
    next_job = enqueue(
        session,
        tenant_id=faker_tenant["tenant_id"],
        kind=JobKind.NOOP.value,
        idempotency_key="next-after-stale",
        run_after=now,
    )
    policy = retry_policy_for(JobKind.NOOP.value)
    stale.status = "RUNNING"
    stale.attempts = policy.max_attempts
    stale.locked_by = "dead-worker"
    stale.locked_at = now - timedelta(minutes=10)
    session.commit()

    claimed = claim_next(
        session,
        locked_by="worker-b",
        now=now,
        lease_seconds=60,
    )
    assert claimed is not None
    assert claimed.id == next_job.id
    session.refresh(stale)
    assert stale.status == "FAILED"
    assert stale.locked_by is None
    assert "LEASE_EXPIRED_MAX_ATTEMPTS" in (stale.last_error or "")


def test_retryable_failure_requeues_with_backoff_then_exhausts(
    monkeypatch, session, faker_tenant
):
    def failing_handler(_session, _tenant_id, _payload):
        raise ConnectionError("temporary upstream outage")

    monkeypatch.setattr(worker_module, "get_handler", lambda _kind: failing_handler)
    queued = enqueue(
        session,
        tenant_id=faker_tenant["tenant_id"],
        kind=JobKind.NOOP.value,
        idempotency_key="retryable-upstream",
    )
    session.commit()
    policy = retry_policy_for(JobKind.NOOP.value)

    for attempt in range(1, policy.max_attempts + 1):
        current = session.get(OutboxJob, queued.id)
        assert current is not None
        current.run_after = datetime.now(tz=UTC) - timedelta(seconds=1)
        session.commit()

        row, result = run_once(locked_by="retry-worker")
        assert row is not None
        assert result is None
        assert row.attempts == attempt
        if attempt < policy.max_attempts:
            assert row.status == "PENDING"
            assert row.locked_by is None
            assert "RETRYABLE" in (row.last_error or "")
        else:
            assert row.status == "FAILED"
            assert "RETRY_EXHAUSTED" in (row.last_error or "")


def test_domain_validation_failure_is_terminal_without_retry(
    monkeypatch, session, faker_tenant
):
    def invalid_handler(_session, _tenant_id, _payload):
        raise DomainError("invalid payload")

    monkeypatch.setattr(worker_module, "get_handler", lambda _kind: invalid_handler)
    queued = enqueue(
        session,
        tenant_id=faker_tenant["tenant_id"],
        kind=JobKind.NOOP.value,
        idempotency_key="terminal-domain-error",
    )
    session.commit()

    row, result = run_once(locked_by="terminal-worker")
    assert row is not None
    assert row.id == queued.id
    assert result is None
    assert row.status == "FAILED"
    assert row.attempts == 1
    assert row.locked_by is None
    assert "TERMINAL" in (row.last_error or "")
