"""Small privacy-safe operational metrics registry.

The standalone candidate deliberately avoids a heavyweight telemetry dependency.
Request metrics are process-local counters (normal Prometheus process semantics),
while durable queue/projection gauges are read from PostgreSQL at scrape time.
No tenant, store, person, payroll amount, correlation id or provider identifier is
used as a metric label.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC
from threading import Lock

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..repositories.models import OutboxJob, SheetProjectionRun

_HTTP_LOCK = Lock()
_HTTP_COUNT: dict[tuple[str, str, str], int] = defaultdict(int)
_HTTP_DURATION_SUM: dict[tuple[str, str, str], float] = defaultdict(float)
_CLOSE_BLOCKERS_LAST = 0
_CLOSE_BLOCKERS_OBSERVATIONS = 0
_JOB_STATUSES = ("PENDING", "RUNNING", "FAILED", "DONE")


def _status_class(status_code: int) -> str:
    return f"{max(1, min(status_code // 100, 5))}xx"


def observe_http_request(
    *, method: str, route: str, status_code: int, duration_seconds: float
) -> None:
    """Record one bounded-cardinality API observation."""

    key = (method.upper(), route, _status_class(status_code))
    with _HTTP_LOCK:
        _HTTP_COUNT[key] += 1
        _HTTP_DURATION_SUM[key] += max(duration_seconds, 0.0)


def observe_close_blockers(count: int) -> None:
    """Record the most recently evaluated final-close blocker count.

    The value is intentionally unlabeled. The authenticated close/checklist API
    remains the place to inspect which tenant/month/entities caused blockers.
    """

    global _CLOSE_BLOCKERS_LAST, _CLOSE_BLOCKERS_OBSERVATIONS
    with _HTTP_LOCK:
        _CLOSE_BLOCKERS_LAST = max(count, 0)
        _CLOSE_BLOCKERS_OBSERVATIONS += 1


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _sample(name: str, value: int | float, labels: dict[str, str] | None = None) -> str:
    suffix = ""
    if labels:
        pairs = ",".join(f'{key}="{_label(item)}"' for key, item in sorted(labels.items()))
        suffix = "{" + pairs + "}"
    return f"{name}{suffix} {value}"


def render_metrics(session: Session) -> str:
    """Render Prometheus text format without business-identity labels."""

    lines = [
        "# HELP ugrile_http_requests_total HTTP requests handled by route template.",
        "# TYPE ugrile_http_requests_total counter",
        "# HELP ugrile_http_request_duration_seconds_sum Cumulative request latency.",
        "# TYPE ugrile_http_request_duration_seconds_sum counter",
    ]
    with _HTTP_LOCK:
        counts = dict(_HTTP_COUNT)
        durations = dict(_HTTP_DURATION_SUM)
        close_blockers = _CLOSE_BLOCKERS_LAST
        close_observations = _CLOSE_BLOCKERS_OBSERVATIONS

    for method, route, status_class in sorted(counts):
        labels = {"method": method, "route": route, "status_class": status_class}
        key = (method, route, status_class)
        lines.append(_sample("ugrile_http_requests_total", counts[key], labels))
        lines.append(
            _sample(
                "ugrile_http_request_duration_seconds_sum",
                round(durations[key], 6),
                labels,
            )
        )

    lines.extend(
        [
            "# HELP ugrile_close_blockers_last_observed Last final-close blocker count.",
            "# TYPE ugrile_close_blockers_last_observed gauge",
            _sample("ugrile_close_blockers_last_observed", close_blockers),
            "# HELP ugrile_close_blocker_observations_total Final-close evaluations observed.",
            "# TYPE ugrile_close_blocker_observations_total counter",
            _sample("ugrile_close_blocker_observations_total", close_observations),
        ]
    )

    try:
        rows = session.execute(
            select(OutboxJob.status, func.count()).group_by(OutboxJob.status)
        ).all()
        by_status = {str(status): int(count) for status, count in rows}
        retry_backlog = int(
            session.scalar(
                select(func.count())
                .select_from(OutboxJob)
                .where(OutboxJob.status == "PENDING", OutboxJob.attempts > 0)
            )
            or 0
        )
        latest_projection = session.scalar(
            select(func.max(SheetProjectionRun.created_at)).where(
                SheetProjectionRun.status == "DONE"
            )
        )
        projection_failures = int(
            session.scalar(
                select(func.count())
                .select_from(SheetProjectionRun)
                .where(SheetProjectionRun.status == "FAILED")
            )
            or 0
        )
    except Exception:
        # A failed PostgreSQL statement can poison the transaction. Metrics are
        # read-only, so rollback before returning the degraded scrape.
        session.rollback()
        lines.extend(
            [
                "# HELP ugrile_metrics_database_up Whether durable metric gauges were readable.",
                "# TYPE ugrile_metrics_database_up gauge",
                _sample("ugrile_metrics_database_up", 0),
            ]
        )
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "# HELP ugrile_metrics_database_up Whether durable metric gauges were readable.",
            "# TYPE ugrile_metrics_database_up gauge",
            _sample("ugrile_metrics_database_up", 1),
            "# HELP ugrile_jobs_current Durable jobs by current state.",
            "# TYPE ugrile_jobs_current gauge",
        ]
    )
    for status in _JOB_STATUSES:
        lines.append(_sample("ugrile_jobs_current", by_status.get(status, 0), {"status": status}))
    lines.extend(
        [
            "# HELP ugrile_job_retry_backlog Current PENDING jobs that have already attempted execution.",
            "# TYPE ugrile_job_retry_backlog gauge",
            _sample("ugrile_job_retry_backlog", retry_backlog),
            "# HELP ugrile_sheet_projection_failures_current Persisted failed projection runs.",
            "# TYPE ugrile_sheet_projection_failures_current gauge",
            _sample("ugrile_sheet_projection_failures_current", projection_failures),
            "# HELP ugrile_sheet_projection_last_success_age_seconds Age of latest successful projection; -1 means none.",
            "# TYPE ugrile_sheet_projection_last_success_age_seconds gauge",
        ]
    )
    age_seconds = -1.0
    if latest_projection is not None:
        if latest_projection.tzinfo is None:
            latest_projection = latest_projection.replace(tzinfo=UTC)
        age_seconds = max((__import__("datetime").datetime.now(tz=UTC) - latest_projection).total_seconds(), 0.0)
    lines.append(_sample("ugrile_sheet_projection_last_success_age_seconds", round(age_seconds, 3)))
    return "\n".join(lines) + "\n"


__all__ = ["observe_close_blockers", "observe_http_request", "render_metrics"]
