"""Domain-level error hierarchy.

The hierarchy is shaped so that the API layer can map domain errors to HTTP
codes without inspecting free-form strings. The fixture connector and
repository raise these errors directly; the worker observes them and emits
typed job status updates.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for all domain errors."""

    code: str = "DOMAIN_ERROR"
    http_status: int = 400

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(DomainError):
    code = "NOT_FOUND"
    http_status = 404


class ValidationError(DomainError):
    code = "VALIDATION_ERROR"
    http_status = 422


class ConflictError(DomainError):
    """Raised when a write would violate a business invariant.

    Used for both domain-level coverage invariants (AC-02) and concurrency
    conflicts (revision stale). The DB layer re-raises this for partial-unique
    index violations.
    """

    code = "CONFLICT"
    http_status = 409


class StaleRevisionError(ConflictError):
    code = "STALE_REVISION"
    http_status = 409


class CoverageInvariantError(ConflictError):
    """AC-02 violation: one agent per store/day or one store per agent/day."""

    code = "COVERAGE_INVARIANT"
    http_status = 409


class ScopeError(DomainError):
    code = "FORBIDDEN"
    http_status = 403


class ConnectorError(DomainError):
    code = "CONNECTOR_ERROR"
    http_status = 400


class AuthError(DomainError):
    code = "AUTH_ERROR"
    http_status = 401
