"""Request/job correlation context shared by API, audit and worker flows."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from structlog.contextvars import bind_contextvars, unbind_contextvars

CORRELATION_HEADER = "X-Correlation-ID"
_MAX_LENGTH = 128
_VALID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_current: ContextVar[str | None] = ContextVar("ugrile_correlation_id", default=None)


def accepted_correlation_id(value: object) -> str | None:
    """Return a safe persisted/header correlation id, or ``None`` when invalid."""

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > _MAX_LENGTH or _VALID.fullmatch(candidate) is None:
        return None
    return candidate


def resolve_request_correlation_id(value: str | None) -> str:
    """Preserve a valid caller id; otherwise issue a server-owned request id."""

    return accepted_correlation_id(value) or f"req_{uuid4().hex}"


def current_correlation_id() -> str | None:
    return _current.get()


@contextmanager
def bind_correlation_id(value: str | None) -> Iterator[None]:
    """Bind correlation to Python + structlog context and restore nesting safely."""

    previous = _current.get()
    token = _current.set(value)
    if value is None:
        unbind_contextvars("correlation_id")
    else:
        bind_contextvars(correlation_id=value)
    try:
        yield
    finally:
        _current.reset(token)
        if previous is None:
            unbind_contextvars("correlation_id")
        else:
            bind_contextvars(correlation_id=previous)


__all__ = [
    "CORRELATION_HEADER",
    "accepted_correlation_id",
    "bind_correlation_id",
    "current_correlation_id",
    "resolve_request_correlation_id",
]
