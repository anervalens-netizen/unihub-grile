"""PII-safe structured logging helpers built on structlog.

Operational logs are intentionally fail-closed: only an explicit set of scalar
fields is allowed into the JSON event. Request/job correlation is bound through
``structlog.contextvars`` so API and durable-worker logs can be connected
without logging request bodies, query strings, identities or payroll values.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from .config import get_settings

_SAFE_LOG_FIELDS = frozenset(
    {
        "event",
        "log_level",
        "timestamp",
        "correlation_id",
        "method",
        "route",
        "status_code",
        "duration_ms",
        "job_id",
        "kind",
        "attempts",
        "max_attempts",
        "retry_in_seconds",
        "retryable",
        "status",
        "locked_by",
        "expected_locked_by",
        "processed",
        "done",
        "retried",
        "failed",
        "empty_polls",
        "error_type",
        "error_code",
    }
)
_MAX_STRING_LENGTH = 256
_SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def pii_safe_event(
    _logger: object,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> dict[str, Any]:
    """Keep only allowlisted scalar operational fields.

    Unknown keys are dropped rather than heuristically redacted. This prevents a
    future log call from accidentally serializing request bodies, emails,
    person/store payloads, payroll amounts, provider credentials or arbitrary
    exception messages. Strings are bounded to avoid log amplification.
    """

    sanitized: dict[str, Any] = {}
    for key in _SAFE_LOG_FIELDS:
        if key not in event_dict:
            continue
        value = event_dict[key]
        if isinstance(value, str):
            sanitized[key] = value[:_MAX_STRING_LENGTH]
        elif value is None or isinstance(value, int | float | bool):
            sanitized[key] = value
    return sanitized


def safe_exception_fields(exc: Exception) -> dict[str, str]:
    """Return diagnostic exception metadata without the exception message.

    Domain/provider error messages can contain data values. Logs therefore keep
    only the Python exception class and, when available, a conservative typed
    error code. Full operator diagnostics remain in their existing controlled
    state (API error envelope / durable job ``last_error``), not in stdout logs.
    """

    fields = {"error_type": exc.__class__.__name__[:_MAX_STRING_LENGTH]}
    candidate: object = None
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        candidate = details.get("code")
    if candidate is None:
        candidate = getattr(exc, "code", None)
    if isinstance(candidate, str) and _SAFE_ERROR_CODE.fullmatch(candidate):
        fields["error_code"] = candidate
    return fields


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            pii_safe_event,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


__all__ = [
    "configure_logging",
    "get_logger",
    "pii_safe_event",
    "safe_exception_fields",
]
