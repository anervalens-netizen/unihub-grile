"""One durable worker.

The worker is the single process that consumes the ``outbox_jobs`` table. It
is designed for synchronous use during S1 (one in-process loop) so the API
can prove the read/write separation and so later stages can replace the loop
with an OS scheduler without touching the job registry.

* ``claim_next`` locks the next pending row using ``SELECT ... FOR UPDATE
  SKIP LOCKED`` semantics — every PostgreSQL version we target supports
  this, and SQLite in tests serialises naturally.
* ``run_once`` is the unit of work exposed to the API and tests; it commits
  on success and marks the row FAILED on domain errors.
* ``run_forever`` is the production loop with a small poll interval.
"""

from __future__ import annotations

import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.database import session_scope
from ..core.logging import get_logger
from ..domain.enums import JobKind
from ..domain.errors import DomainError
from ..repositories.models import OutboxJob
from .jobs import JobResult, get_handler

log = get_logger("ugrile.worker")


@dataclass(slots=True)
class RunSummary:
    processed: int = 0
    done: int = 0
    failed: int = 0
    empty_polls: int = 0


def claim_next(session: Session, *, locked_by: str) -> OutboxJob | None:
    """Atomically claim the next pending job.

    Uses ``with_for_update(skip_locked=True)`` on PostgreSQL; SQLite ignores
    the option but still serialises because of the global write lock.
    """

    stmt = (
        select(OutboxJob)
        .where(OutboxJob.status == "PENDING")
        .order_by(OutboxJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        return None
    row.status = "RUNNING"
    row.attempts += 1
    row.locked_at = _utcnow()
    row.locked_by = locked_by
    session.flush()
    return row


def run_once(*, locked_by: str = "ugrile-worker") -> tuple[OutboxJob | None, JobResult | None]:
    """Process a single job inside its own transaction.

    Returns the row and the result. ``row`` is ``None`` when there is no
    pending work; ``result`` is ``None`` when the row failed (the exception
    is re-raised to the caller for visibility).
    """

    handler_dispatch: dict[str, Any] = {}
    with session_scope() as session:
        row = claim_next(session, locked_by=locked_by)
        if row is None:
            return None, None
        handler_dispatch["row"] = row
        try:
            handler = get_handler(row.kind)
            payload = _parse_payload(row.payload)
            result = handler(session, payload)
        except DomainError as exc:
            row.status = "FAILED"
            row.last_error = f"{exc.code}: {exc.message}"
            log.warning("job_failed", kind=row.kind, error=exc.code, message=exc.message)
            return row, None
        except Exception as exc:  # pragma: no cover - last-resort guard
            row.status = "FAILED"
            row.last_error = f"UNEXPECTED: {exc!r}"
            log.error("job_crashed", kind=row.kind, error=repr(exc))
            return row, None
        else:
            row.status = result.status
            row.last_error = None
            return row, result


def run_once_safe() -> tuple[OutboxJob | None, JobResult | None]:
    """``run_once`` wrapper that swallows exceptions for the loop body."""

    try:
        return run_once()
    except Exception as exc:  # pragma: no cover - last-resort guard
        log.error("worker_iteration_crashed", error=repr(exc))
        return None, None


def run_forever(*, stop_after_empty_polls: int | None = None) -> RunSummary:
    """Drive the worker loop until SIGINT or N empty polls.

    ``stop_after_empty_polls`` is the unit-test entry point — production
    deployments run ``run_forever()`` under a process supervisor.
    """

    settings = get_settings()
    stop_flag = {"stop": False}

    def _signal(_signum: int, _frame: object) -> None:  # pragma: no cover - signal handler
        stop_flag["stop"] = True

    signal.signal(signal.SIGINT, _signal)
    signal.signal(signal.SIGTERM, _signal)

    summary = RunSummary(processed=0, done=0, failed=0, empty_polls=0)
    while not stop_flag["stop"]:
        row, result = run_once_safe()
        if row is None:
            summary.empty_polls += 1
            if stop_after_empty_polls is not None and (
                summary.empty_polls >= stop_after_empty_polls
            ):
                break
            time.sleep(settings.worker_poll_seconds)
            continue
        summary.empty_polls = 0
        summary.processed += 1
        if result is None:
            summary.failed += 1
        else:
            summary.done += 1
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
    """Insert a job row. Caller is responsible for committing."""

    if kind not in JobKind.__members__.values() and kind not in [k.value for k in JobKind]:
        # Allow passing enum members or their values.
        kind = JobKind(kind).value
    row = OutboxJob(
        tenant_id=tenant_id,
        kind=kind,
        idempotency_key=idempotency_key,
        payload=_stringify_payload(payload or {}),
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


__all__ = [
    "RunSummary",
    "claim_next",
    "enqueue",
    "run_once",
    "run_once_safe",
    "run_forever",
]
