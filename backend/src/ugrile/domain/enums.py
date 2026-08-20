"""Domain enumerations.

These types are the contract between the API, services and DB. Every value is
frozen (string-keyed) so logs, fixtures, and projections stay stable.
"""

from __future__ import annotations

from enum import StrEnum


class WorkingKind(StrEnum):
    """Classification of a working day per person-at-site-day."""

    NORMAL = "NORMAL"
    EXTRA_HOME = "EXTRA_HOME"
    EXTRA_OTHER = "EXTRA_OTHER"


class DayStatus(StrEnum):
    """Effective status of a day at a site or for a person."""

    WORKING = "WORKING"
    OFF = "OFF"
    LEAVE = "LEAVE"


class MonthState(StrEnum):
    """Lifecycle of a calendar month."""

    DRAFT = "DRAFT"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"


class RoleName(StrEnum):
    """Application-level roles."""

    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    READONLY = "READONLY"


class ConnectorGeneration(StrEnum):
    """Source connector generations recognised at S1."""

    FIXTURE_V1 = "FIXTURE_V1"


class JobKind(StrEnum):
    """Recognised job kinds for the durable worker."""

    FIXTURE_INGEST = "FIXTURE_INGEST"
    TENANT_BOOTSTRAP = "TENANT_BOOTSTRAP"
    NOOP = "NOOP"
