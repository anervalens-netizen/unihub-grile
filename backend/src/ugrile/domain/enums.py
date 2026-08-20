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


class EpayCategory(StrEnum):
    """E-pay observation categories (per agent per store)."""

    UNDER_50 = "UNDER_50"
    AT_OR_OVER_50 = "AT_OR_OVER_50"


class CloseBlockerCode(StrEnum):
    """Typed close blockers.

    Each blocker represents a deterministic precondition that a month must
    satisfy before close. Anything that depends on stages beyond S3 (full E-pay
    refresh, signed Sheet canary readback, external reconciliation) is added
    here as a typed code so the integrator can wire the missing check later
    without bypassing existing ones.
    """

    STORE_DAY_UNCOVERED = "STORE_DAY_UNCOVERED"
    STORE_DAY_MULTIPLE_WORKING = "STORE_DAY_MULTIPLE_WORKING"
    PERSON_DAY_MULTIPLE_WORKING = "PERSON_DAY_MULTIPLE_WORKING"
    INVALID_WORKING_KIND = "INVALID_WORKING_KIND"
    SALES_MISSING_FOR_WORKED_DAY = "SALES_MISSING_FOR_WORKED_DAY"
    SALES_ORPHAN_FOR_COVERED_DAY = "SALES_ORPHAN_FOR_COVERED_DAY"
    TARGET_ZERO_FOR_WORKED_STORE = "TARGET_ZERO_FOR_WORKED_STORE"
    EPAY_FRESH_READBACK_REQUIRED = "EPAY_FRESH_READBACK_REQUIRED"
    SHEET_CANARY_REQUIRED = "SHEET_CANARY_REQUIRED"
    EXTERNAL_RECONCILIATION_REQUIRED = "EXTERNAL_RECONCILIATION_REQUIRED"


class CloseAction(StrEnum):
    """Append-only close audit actions."""

    CLOSE = "CLOSE"
    REOPEN = "REOPEN"


__all__ = [
    "CloseAction",
    "CloseBlockerCode",
    "ConnectorGeneration",
    "DayStatus",
    "EpayCategory",
    "JobKind",
    "MonthState",
    "RoleName",
    "WorkingKind",
]
