"""Deterministic retry policy for durable outbox jobs.

The queue stores attempts/run-after/lease state; this module owns only policy.
Business/domain validation errors remain terminal in the worker. Retry policy is
used for explicitly retryable provider/infrastructure failures and for stale
RUNNING recovery after a process crash.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import JobKind


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    base_delay_seconds: int
    max_delay_seconds: int


_DEFAULT = RetryPolicy(max_attempts=3, base_delay_seconds=2, max_delay_seconds=60)
_POLICIES: dict[str, RetryPolicy] = {
    JobKind.GOOGLE_PROJECTION_STORE.value: RetryPolicy(
        max_attempts=5,
        base_delay_seconds=5,
        max_delay_seconds=300,
    ),
    JobKind.EXPORT_XLSX_STORE.value: RetryPolicy(
        max_attempts=3,
        base_delay_seconds=2,
        max_delay_seconds=60,
    ),
    JobKind.EXPORT_XLSX_BULK.value: RetryPolicy(
        max_attempts=3,
        base_delay_seconds=5,
        max_delay_seconds=120,
    ),
    JobKind.EXPORT_PONTAJ_ONLY.value: RetryPolicy(
        max_attempts=3,
        base_delay_seconds=2,
        max_delay_seconds=60,
    ),
}


def retry_policy_for(kind: str) -> RetryPolicy:
    """Return the bounded retry policy for one job kind."""

    return _POLICIES.get(kind, _DEFAULT)


def retry_delay_seconds(kind: str, attempts: int) -> int:
    """Return deterministic exponential backoff after ``attempts`` claims.

    ``attempts`` is 1-based because the row increments when a lease is claimed.
    No jitter is used: deterministic state is more useful than synchronized
    randomness for this single-authority worker, and the cap bounds recovery.
    """

    policy = retry_policy_for(kind)
    exponent = max(attempts - 1, 0)
    delay = int(policy.base_delay_seconds * (2**exponent))
    return min(delay, policy.max_delay_seconds)


__all__ = ["RetryPolicy", "retry_delay_seconds", "retry_policy_for"]
