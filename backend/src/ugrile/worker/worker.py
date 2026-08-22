"""Durable outbox worker with committed leases and bounded recovery.

The worker is the single authority that executes ``outbox_jobs``. Claims are
committed before handler execution so a process crash leaves visible RUNNING
state instead of an invisible transaction. A later worker deterministically
recovers an expired lease, while active handlers keep the row locked for their
execution transaction so another worker cannot steal a long-running live job.

Queue guarantees implemented here:

* only due ``PENDING`` jobs (``run_after <= now``) can be claimed;
* claims use ``SELECT .. FOR UPDATE SKIP LOCKED`` and persist attempts/lease;
* stale ``RUNNING`` jobs are requeued after the configured lease timeout, or
  terminally failed once their retry budget is exhausted;
* explicitly retryable provider/infrastructure failures use bounded
  deterministic exponential backoff;
* business/domain validation failures are terminal and are never retried;
* handler-authored failure diagnostics (for example last-good Google state or
  an ExportRun FAILED marker) commit together with job settlement when the
  transaction is healthy; a poisoned DB transaction falls back to a clean
  settlement transaction.
"""

from __future__ import annotations

import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.correlation import (
    accepted_correlation_id,
    bind_correlation_id,
    current_correlation_id,
)
from ..core.database import session_scope
from ..core.logging import get_logger
from ..domain.enums import JobKind
from ..domain.errors import DomainError
from ..repositories.models import OutboxJob
from .jobs import JobResult, get_handler
from .policy import retry_delay_seconds, retry_policy_for

log = get_logger("ugrile.worker")

WORKER_LOCKED_BY = "ugrile-worker"


@dataclass(slots=True)
class RunSummary:
    processed: int = 0
    done: int = 0
    retried: int = 0
    failed: int = 0
    empty_polls: int = 0


def recover_stale_jobs(
    session: Session,
    *,
    now: datetime | None = None,
    lease_seconds: int | None = None,
) -> tuple[int, int]:
    """Recover expired RUNNING leases.

    Returns ``(requeued, failed)``. A stale row below its per-kind attempt budget
    becomes immediately due ``PENDING`` work. Once the budget is exhausted it
    becomes terminal ``FAILED`` so no job can remain stranded forever.
    """

    now_value = now or _utcnow()
    lease = lease_seconds or get_settings().worker_lease_seconds
    cutoff = now_value - timedelta(seconds=lease)
    rows = list(
        session.execute(
            select(OutboxJob)
            .where(
                OutboxJob.status == "RUNNING",
                or_(OutboxJob.locked_at.is_(None), OutboxJob.locked_at <= cutoff),
            )
            .order_by(OutboxJob.id)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    requeued = 0
    failed = 0
    for row in rows:
        policy = retry_policy_for(row.kind)
        previous_worker = row.locked_by or "unknown"
        if row.attempts >= policy.max_attempts:
            row.status = "FAILED"
            row.last_error = (
                "LEASE_EXPIRED_MAX_ATTEMPTS: "
                f"worker={previous_worker}; attempts={row.attempts}/{policy.max_attempts}"
            )
            failed += 1
        else:
            row.status = "PENDING"
            row.run_after = now_value
            row.last_error = (
                "LEASE_EXPIRED: "
                f"worker={previous_worker}; recovering attempt {row.attempts}"
            )
            requeued += 1
        row.locked_at = None
        row.locked_by = None
    if rows:
        session.flush()
    return requeued, failed


def claim_next(
    session: Session,
    *,
    locked_by: str,
    now: datetime | None = None,
    lease_seconds: int | None = None,
) -> OutboxJob | None:
    """Atomically claim the next due job after stale-lease recovery.

    The caller owns the transaction. ``run_once`` commits this claim before
    handler execution; direct tests/operators may also call it transactionally.
    """

    now_value = now or _utcnow()
    recover_stale_jobs(
        session,
        now=now_value,
        lease_seconds=lease_seconds,
    )
    stmt = (
        select(OutboxJob)
        .where(
            OutboxJob.status == "PENDING",
            OutboxJob.run_after <= now_value,
        )
        .order_by(OutboxJob.run_after, OutboxJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        return None
    row.status = "RUNNING"
    row.attempts += 1
    row.locked_at = now_value
    row.locked_by = locked_by
    session.flush()
    return row


def _retryable_exception(exc: Exception) -> bool:
    """Classify failures that are safe to retry under the job idempotency contract."""

    from ..connectors.google import GoogleAdapterError

    if isinstance(exc, GoogleAdapterError):
        return True
    return isinstance(exc, ConnectionError | TimeoutError | OSError | OperationalError)


def _error_text(exc: Exception) -> str:
    if isinstance(exc, DomainError):
        detail_code = exc.details.get("code")
        code = detail_code if isinstance(detail_code, str) and detail_code else exc.code
        return f"{code}: {exc.message}"
    text = str(exc)
    return f"{exc.__class__.__name__}: {text if text else repr(exc)}"


def _settle_failure(row: OutboxJob, exc: Exception, *, now: datetime) -> None:
    """Move one RUNNING row to retryable PENDING or terminal FAILED."""

    policy = retry_policy_for(row.kind)
    error = _error_text(exc)
    retryable = _retryable_exception(exc)
    if retryable and row.attempts < policy.max_attempts:
        delay = retry_delay_seconds(row.kind, row.attempts)
        row.status = "PENDING"
        row.run_after = now + timedelta(seconds=delay)
        row.last_error = (
            f"RETRYABLE attempt={row.attempts}/{policy.max_attempts} "
            f"retry_in={delay}s: {error}"
        )
        log.warning(
            "job_retry_scheduled",
            kind=row.kind,
            attempts=row.attempts,
            max_attempts=policy.max_attempts,
            retry_in_seconds=delay,
            error=error,
        )
    else:
        row.status = "FAILED"
        reason = "RETRY_EXHAUSTED" if retryable else "TERMINAL"
        row.last_error = f"{reason} attempt={row.attempts}/{policy.max_attempts}: {error}"
        log.warning(
            "job_failed",
            kind=row.kind,
            attempts=row.attempts,
            max_attempts=policy.max_attempts,
            retryable=retryable,
            error=error,
        )
    row.locked_at = None
    row.locked_by = None


def _settle_in_clean_transaction(
    *,
    job_id: int,
    locked_by: str,
    exc: Exception,
) -> OutboxJob | None:
    """Fallback settlement after the handler transaction itself became unusable."""

    with session_scope() as settlement_session:
        row = settlement_session.execute(
            select(OutboxJob).where(OutboxJob.id == job_id).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.status == "RUNNING" and row.locked_by == locked_by:
            _settle_failure(row, exc, now=_utcnow())
            settlement_session.flush()
        settled = row
    return settled


def run_once(
    *, locked_by: str = WORKER_LOCKED_BY
) -> tuple[OutboxJob | None, JobResult | None]:
    """Claim, commit the lease, then execute exactly one due job.

    Claim and execution use separate transactions. A normal handler failure is
    settled inside the execution transaction so handler-authored diagnostic
    rows survive. If that transaction cannot flush/commit, it is rolled back and
    only the outbox state is settled in a fresh transaction.
    """

    with session_scope() as claim_session:
        claimed = claim_next(claim_session, locked_by=locked_by)
        if claimed is None:
            return None, None
        job_id = claimed.id

    handler_error: Exception | None = None
    execution_row: OutboxJob | None = None
    result: JobResult | None = None
    job_correlation_id: str | None = None
    try:
        with session_scope() as execution_session:
            row = execution_session.execute(
                select(OutboxJob).where(OutboxJob.id == job_id).with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return None, None
            if row.status != "RUNNING" or row.locked_by != locked_by:
                log.warning(
                    "job_lease_lost_before_execution",
                    job_id=job_id,
                    status=row.status,
                    locked_by=row.locked_by,
                    expected_locked_by=locked_by,
                )
                return row, None

            payload = _parse_payload(row.payload)
            job_correlation_id = accepted_correlation_id(payload.get("correlation_id"))
            with bind_correlation_id(job_correlation_id):
                try:
                    handler = get_handler(row.kind)
                    result = handler(execution_session, row.tenant_id, payload)
                except Exception as exc:
                    handler_error = exc
                    _settle_failure(row, exc, now=_utcnow())
                else:
                    row.status = result.status
                    row.last_error = None
                    row.locked_at = None
                    row.locked_by = None
                execution_session.flush()
                execution_row = row
    except Exception as transaction_exc:
        settlement_error = handler_error or transaction_exc
        with bind_correlation_id(job_correlation_id):
            settled = _settle_in_clean_transaction(
                job_id=job_id,
                locked_by=locked_by,
                exc=settlement_error,
            )
        return settled or claimed, None

    if handler_error is not None:
        return execution_row or claimed, None
    return execution_row or claimed, result


def run_once_safe() -> tuple[OutboxJob | None, JobResult | None]:
    """``run_once`` wrapper that keeps the process loop alive on infrastructure errors."""

    try:
        return run_once()
    except Exception as exc:  # pragma: no cover - database/process guard
        log.error("worker_iteration_crashed", error=repr(exc))
        return None, None


def run_forever(*, stop_after_empty_polls: int | None = None) -> RunSummary:
    """Drive the worker loop until SIGINT/SIGTERM or N empty polls."""

    settings = get_settings()
    stop_flag = {"stop": False}

    def _signal(_signum: int, _frame: object) -> None:  # pragma: no cover - signal handler
        stop_flag["stop"] = True

    signal.signal(signal.SIGINT, _signal)
    signal.signal(signal.SIGTERM, _signal)

    summary = RunSummary()
    while not stop_flag["stop"]:
        row, result = run_once_safe()
        if row is None:
            summary.empty_polls += 1
            if stop_after_empty_polls is not None and summary.empty_polls >= stop_after_empty_polls:
                break
            time.sleep(settings.worker_poll_seconds)
            continue
        summary.empty_polls = 0
        summary.processed += 1
        if result is not None and row.status == "DONE":
            summary.done += 1
        elif row.status == "PENDING":
            summary.retried += 1
        else:
            summary.failed += 1
    return summary


def enqueue(
    session: Session,
    *,
    tenant_id: str,
    kind: str,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
    run_after: datetime | None = None,
) -> OutboxJob:
    """Insert a job row. Caller is responsible for committing.

    The current API/worker correlation id is copied into the durable payload.
    It is diagnostic metadata only and never participates in the scoped
    idempotency key.
    """

    if kind not in JobKind.__members__.values() and kind not in [k.value for k in JobKind]:
        kind = JobKind(kind).value
    job_payload = dict(payload or {})
    correlation_id = current_correlation_id()
    if correlation_id is not None:
        job_payload["correlation_id"] = correlation_id
    row = OutboxJob(
        tenant_id=tenant_id,
        kind=kind,
        idempotency_key=idempotency_key,
        payload=_stringify_payload(job_payload),
        status="PENDING",
        run_after=run_after or _utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def _parse_payload(raw: str) -> dict[str, Any]:
    import json

    if not raw:
        return {}
    try:
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise DomainError("job payload must be a JSON object", details={"raw": raw})
        return result
    except json.JSONDecodeError as exc:
        raise DomainError("job payload is not valid JSON", details={"raw": raw}) from exc


def _stringify_payload(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def main() -> RunSummary:
    """Executable durable-worker entrypoint."""

    log.info("worker_started", locked_by=WORKER_LOCKED_BY)
    summary = run_forever()
    log.info(
        "worker_stopped",
        processed=summary.processed,
        done=summary.done,
        retried=summary.retried,
        failed=summary.failed,
        empty_polls=summary.empty_polls,
    )
    return summary


if __name__ == "__main__":
    main()


__all__ = [
    "RunSummary",
    "WORKER_LOCKED_BY",
    "claim_next",
    "enqueue",
    "main",
    "recover_stale_jobs",
    "run_once",
    "run_once_safe",
    "run_forever",
]
