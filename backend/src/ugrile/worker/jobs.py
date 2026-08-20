"""Typed job registry.

Each job kind has a handler that takes a parsed payload and returns a result
dict. The worker is the only consumer of this module; it ensures that every
dispatched job has a registered handler, that the dispatch raises no
unhandled exception (so the row can move to DONE/FAILED atomically), and
that the handler result is persisted as part of the job row.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..connectors.fixtures import default_fixture
from ..connectors.ingest import FixtureConnector
from ..domain.enums import JobKind
from ..domain.errors import DomainError


@dataclass(frozen=True, slots=True)
class JobResult:
    status: str
    payload: dict[str, Any]


JobHandler = Callable[[Session, dict[str, Any]], JobResult]


def _job_fixture_ingest(session: Session, payload: dict[str, Any]) -> JobResult:
    """Apply the canonical fixture (no live source yet).

    The fixture is intentionally hardcoded: S1 must not import from Retail
    and the connector boundary test enforces this. A future ``FIXTURE_V1``
    job may accept a payload override once fixtures grow.
    """

    connector = FixtureConnector(session)
    summary = connector.apply(default_fixture())
    return JobResult(status="DONE", payload={"summary": dict(summary)})


def _job_tenant_bootstrap(session: Session, payload: dict[str, Any]) -> JobResult:
    from ..domain.identifiers import make_tenant_id
    from ..repositories.months import MonthRepository
    from ..repositories.tenants import TenantRepository

    tenant_token = payload.get("tenant_token", "fixture")
    year = int(payload.get("year", 2026))
    month = int(payload.get("month", 8))
    tenant_id = make_tenant_id(tenant_token)
    tenants = TenantRepository(session)
    tenants.upsert(
        tenant_id=tenant_id,
        name=tenant_token.title(),
        timezone=payload.get("timezone", "Europe/Bucharest"),
    )
    months = MonthRepository(session)
    m = months.get_or_create(tenant_id=tenant_id, year=year, month=month)
    return JobResult(
        status="DONE",
        payload={"tenant_id": tenant_id, "month_id": m.id, "revision": m.revision},
    )


def _job_noop(session: Session, payload: dict[str, Any]) -> JobResult:
    return JobResult(status="DONE", payload={"noop": True})


_REGISTRY: dict[str, JobHandler] = {
    JobKind.FIXTURE_INGEST: _job_fixture_ingest,
    JobKind.TENANT_BOOTSTRAP: _job_tenant_bootstrap,
    JobKind.NOOP: _job_noop,
}


def get_handler(kind: str) -> JobHandler:
    try:
        return _REGISTRY[kind]
    except KeyError as exc:
        raise DomainError(f"unknown job kind: {kind}", details={"kind": kind}) from exc


def known_kinds() -> list[str]:
    return sorted(_REGISTRY.keys())


__all__ = [
    "JobHandler",
    "JobResult",
    "get_handler",
    "known_kinds",
]
