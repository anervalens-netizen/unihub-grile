"""Typed job registry.

Each job kind has a handler that takes a parsed payload and returns a result
dict. The worker is the only consumer of this module; it ensures that every
dispatched job has a registered handler, that the dispatch raises no
unhandled exception (so the row can move to DONE/FAILED atomically), and
that the handler result is persisted as part of the job row.

Job authority
-------------

The contract is explicit: the worker is the sole authority for executing
jobs. The API layer is permitted only to **enqueue**; it MUST NOT execute.
This module is therefore reachable from one process only — the durable
worker started by ``python -m ugrile.worker.worker`` or by the S1 in-process
loop inside the same process boundary. The job payload carries the tenant
identifier so the handler can scope its work, regardless of which process
executes the row.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..connectors.fixtures import (
    FIXTURE_TENANT_TOKEN,
    default_fixture,
)
from ..connectors.ingest import FixtureConnector
from ..connectors.v1_types import (
    ConnectorHeader,
    PersonRecord,
    SalesRecord,
    StoreRecord,
    TargetRecord,
)
from ..domain.enums import JobKind
from ..domain.errors import ConnectorError, DomainError
from ..domain.identifiers import (
    make_tenant_id,
    tenant_slug_from_tenant_id,
)


@dataclass(frozen=True, slots=True)
class JobResult:
    status: str
    payload: dict[str, Any]


JobHandler = Callable[[Session, str, dict[str, Any]], JobResult]


def _build_payload_for_tenant(tenant_id: str) -> Any:
    """Return a fixture rewritten to land under ``tenant_id``.

    The connector is tenant-scoped: it always upserts by
    ``(tenant_id, internal_code)``. A job that enqueues with a different
    tenant token must rewrite the fixture header and per-record
    ``tenant_id`` fields so the rows land under the right tenant.
    """

    fx = default_fixture()
    if fx.header.tenant_id == tenant_id:
        return fx

    bare_token = tenant_slug_from_tenant_id(tenant_id)
    if bare_token == FIXTURE_TENANT_TOKEN and tenant_id == fx.header.tenant_id:
        return fx

    return fx.model_copy(
        update={
            "header": ConnectorHeader.model_construct(
                schema_name=fx.header.schema_name,
                generation=fx.header.generation,
                tenant_id=tenant_id,
                emitted_at=fx.header.emitted_at,
            ),
            "stores": [
                StoreRecord(**{**r.model_dump(), "tenant_id": bare_token})
                for r in fx.stores
            ],
            "people": [
                PersonRecord(**{**r.model_dump(), "tenant_id": bare_token})
                for r in fx.people
            ],
            "sales": [
                SalesRecord(**{**r.model_dump(), "tenant_id": bare_token})
                for r in fx.sales
            ],
            "targets": [
                TargetRecord(**{**r.model_dump(), "tenant_id": bare_token})
                for r in fx.targets
            ],
        }
    )


def _job_fixture_ingest(
    session: Session, tenant_id: str, payload: dict[str, Any]
) -> JobResult:
    """Apply the canonical fixture, scoped to the job's tenant.

    The previous attempt always applied the canonical ``tenant_fixture``
    payload, which silently overrode the caller-supplied tenant. The job row
    carries the tenant id in its ``tenant_id`` column AND may carry an
    explicit ``tenant_token`` override in the payload; the handler honours
    both.
    """

    override = payload.get("tenant_token")
    target_tenant_id = make_tenant_id(str(override)) if override else tenant_id

    fixture = _build_payload_for_tenant(target_tenant_id)
    connector = FixtureConnector(session)
    try:
        summary = connector.apply(fixture)
    except ConnectorError as exc:
        raise DomainError(
            f"fixture ingest failed: {exc.message}",
            details={"code": exc.code, "details": exc.details},
        ) from exc
    return JobResult(status="DONE", payload={"summary": dict(summary), "tenant": target_tenant_id})


def _job_tenant_bootstrap(
    session: Session, tenant_id: str, payload: dict[str, Any]
) -> JobResult:
    from ..repositories.months import MonthRepository
    from ..repositories.tenants import TenantRepository

    tenant_token = payload.get("tenant_token") or tenant_slug_from_tenant_id(tenant_id)
    year = int(payload.get("year", 2026))
    month = int(payload.get("month", 8))
    target_tenant_id = make_tenant_id(str(tenant_token))
    tenants = TenantRepository(session)
    tenants.upsert(
        tenant_id=target_tenant_id,
        name=str(tenant_token).title(),
        timezone=payload.get("timezone", "Europe/Bucharest"),
    )
    months = MonthRepository(session)
    m = months.get_or_create(tenant_id=target_tenant_id, year=year, month=month)
    return JobResult(
        status="DONE",
        payload={"tenant_id": target_tenant_id, "month_id": m.id, "revision": m.revision},
    )


def _job_noop(session: Session, tenant_id: str, payload: dict[str, Any]) -> JobResult:
    return JobResult(status="DONE", payload={"noop": True, "tenant": tenant_id})


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
