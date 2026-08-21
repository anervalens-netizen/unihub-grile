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

S4 worker extension
-------------------

The S4 slice adds idempotent handlers for the export/projection job
kinds. The handlers are registered now so the manager UI can enqueue them
through the worker API and operators see progress through the existing
``GET /worker/jobs`` endpoint. The actual XLSX/Google adapter work is
owned by S5; until then the handlers persist a typed
``NOT_IMPLEMENTED_S5`` payload and move the row to ``FAILED`` with a
deterministic error so the job loop stays exercised end-to-end.
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
    IncentiveRecord,
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
from ..repositories.models import ExportRun, ImportRun


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
                StoreRecord(**{**r.model_dump(), "tenant_id": bare_token}) for r in fx.stores
            ],
            "people": [
                PersonRecord(**{**r.model_dump(), "tenant_id": bare_token}) for r in fx.people
            ],
            "sales": [SalesRecord(**{**r.model_dump(), "tenant_id": bare_token}) for r in fx.sales],
            "targets": [
                TargetRecord(**{**r.model_dump(), "tenant_id": bare_token}) for r in fx.targets
            ],
            "incentives": [
                IncentiveRecord(**{**r.model_dump(), "tenant_id": bare_token})
                for r in fx.incentives
            ],
        }
    )


def _job_fixture_ingest(session: Session, tenant_id: str, payload: dict[str, Any]) -> JobResult:
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


def _job_tenant_bootstrap(session: Session, tenant_id: str, payload: dict[str, Any]) -> JobResult:
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


def _job_export_scaffold(
    session: Session,
    tenant_id: str,
    payload: dict[str, Any],
    *,
    kind: JobKind,
    label: str,
) -> JobResult:
    """Persist a typed ``ImportRun``/``ExportRun`` stub and return progress.

    The S5 adapter will replace this body with the actual writer; the
    S4 contract requires that enqueue/list/deduplication/SKIP LOCKED work
    end-to-end on the seeded fixture before the adapter lands.
    """

    summary = {
        "tenant_id": tenant_id,
        "kind": kind.value,
        "label": label,
        "stage": "ENQUEUED_S4",
        "idempotency_key": payload.get("idempotency_key") if isinstance(payload, dict) else None,
        "requested": payload,
    }
    if kind in {JobKind.EXPORT_XLSX_STORE, JobKind.EXPORT_XLSX_BULK, JobKind.EXPORT_PONTAJ_ONLY}:
        session.add(
            ExportRun(
                tenant_id=tenant_id,
                kind=kind.value,
                status="ENQUEUED",
                summary=_jsonify(summary),
                artifact_uri=None,
            )
        )
    elif kind == JobKind.GOOGLE_PROJECTION_STORE:
        session.add(
            ImportRun(
                tenant_id=tenant_id,
                kind=kind.value,
                status="ENQUEUED",
                summary=_jsonify(summary),
                errors="[]",
            )
        )
    return JobResult(status="DONE", payload=summary)


def _write_bytes(uri: str, payload: bytes) -> None:
    """Persist the rendered workbook bytes to ``uri`` (a non-tracked path)."""
    import os

    os.makedirs(os.path.dirname(uri), exist_ok=True)
    with open(uri, "wb") as fh:
        fh.write(payload)


def _job_export_xlsx_store(session: Session, tenant_id: str, payload: dict[str, Any]) -> JobResult:
    """S5b real writer for per-store XLSX export."""
    from ..repositories.months import MonthRepository
    from ..services.xlsx_export import (
        record_export_run,
        render_store_export,
    )

    month_id = payload.get("month_id")
    store_id = payload.get("store_id")
    if not month_id or not store_id:
        raise DomainError(
            "month_id and store_id are required",
            details={"payload": payload},
        )
    month = MonthRepository(session).get(month_id)
    if month.tenant_id != tenant_id:
        raise DomainError(
            "tenant mismatch in EXPORT_XLSX_STORE payload",
            details={"month_tenant": month.tenant_id, "tenant": tenant_id},
        )
    envelope = render_store_export(
        session, tenant_id=tenant_id, month=month, store_id=store_id
    )
    artifact_uri = payload.get("artifact_uri_hint") or payload.get("artifact_uri")
    if not artifact_uri:
        # Worker generates a stable non-tracked path; the API hint is preferred
        # because the API knows the canonical filename.
        import os
        import tempfile

        base = os.environ.get("UGR_S5_EXPORT_DIR") or tempfile.gettempdir()
        artifact_uri = os.path.join(base, "ugrile-s5-exports", envelope.filename)
    _write_bytes(artifact_uri, envelope.bytes_)
    record_export_run(
        session,
        tenant_id=tenant_id,
        kind=JobKind.EXPORT_XLSX_STORE.value,
        envelope=envelope,
        artifact_uri=artifact_uri,
    )
    return JobResult(
        status="DONE",
        payload={
            "filename": envelope.filename,
            "checksum_sha256": envelope.checksum,
            "artifact_uri": artifact_uri,
            "summary": envelope.summary,
        },
    )


def _job_export_xlsx_bulk(session: Session, tenant_id: str, payload: dict[str, Any]) -> JobResult:
    """S5b real writer for bulk ZIP export."""
    from ..repositories.months import MonthRepository
    from ..services.xlsx_export import (
        record_export_run,
        render_bulk_export,
    )

    month_id = payload.get("month_id")
    if not month_id:
        raise DomainError(
            "month_id is required",
            details={"payload": payload},
        )
    month = MonthRepository(session).get(month_id)
    if month.tenant_id != tenant_id:
        raise DomainError(
            "tenant mismatch in EXPORT_XLSX_BULK payload",
            details={"month_tenant": month.tenant_id, "tenant": tenant_id},
        )
    store_ids = payload.get("store_ids")
    envelope = render_bulk_export(
        session,
        tenant_id=tenant_id,
        month=month,
        store_ids=[str(s) for s in store_ids] if store_ids else None,
    )
    artifact_uri = payload.get("artifact_uri_hint") or payload.get("artifact_uri")
    if not artifact_uri:
        import os
        import tempfile

        base = os.environ.get("UGR_S5_EXPORT_DIR") or tempfile.gettempdir()
        artifact_uri = os.path.join(base, "ugrile-s5-exports", envelope.filename)
    _write_bytes(artifact_uri, envelope.bytes_)
    record_export_run(
        session,
        tenant_id=tenant_id,
        kind=JobKind.EXPORT_XLSX_BULK.value,
        envelope=envelope,
        artifact_uri=artifact_uri,
    )
    return JobResult(
        status="DONE",
        payload={
            "filename": envelope.filename,
            "checksum_sha256": envelope.checksum,
            "artifact_uri": artifact_uri,
            "summary": envelope.summary,
        },
    )


def _job_export_pontaj_only(session: Session, tenant_id: str, payload: dict[str, Any]) -> JobResult:
    """S5b real writer for the pontaj-only workbook."""
    from ..repositories.months import MonthRepository
    from ..services.xlsx_export import (
        record_export_run,
        render_pontaj_only_export,
    )

    month_id = payload.get("month_id")
    if not month_id:
        raise DomainError(
            "month_id is required",
            details={"payload": payload},
        )
    month = MonthRepository(session).get(month_id)
    if month.tenant_id != tenant_id:
        raise DomainError(
            "tenant mismatch in EXPORT_PONTAJ_ONLY payload",
            details={"month_tenant": month.tenant_id, "tenant": tenant_id},
        )
    store_ids = payload.get("store_ids")
    envelope = render_pontaj_only_export(
        session,
        tenant_id=tenant_id,
        month=month,
        store_ids=[str(s) for s in store_ids] if store_ids else None,
    )
    artifact_uri = payload.get("artifact_uri_hint") or payload.get("artifact_uri")
    if not artifact_uri:
        import os
        import tempfile

        base = os.environ.get("UGR_S5_EXPORT_DIR") or tempfile.gettempdir()
        artifact_uri = os.path.join(base, "ugrile-s5-exports", envelope.filename)
    _write_bytes(artifact_uri, envelope.bytes_)
    record_export_run(
        session,
        tenant_id=tenant_id,
        kind=JobKind.EXPORT_PONTAJ_ONLY.value,
        envelope=envelope,
        artifact_uri=artifact_uri,
    )
    return JobResult(
        status="DONE",
        payload={
            "filename": envelope.filename,
            "checksum_sha256": envelope.checksum,
            "artifact_uri": artifact_uri,
            "summary": envelope.summary,
        },
    )


def _job_google_projection_store(
    session: Session, tenant_id: str, payload: dict[str, Any]
) -> JobResult:
    """S5a fake-adapter projection (AC-12 slice).

    The handler materialises the Grila + Pontaj projection for the
    requested store/month and hands it to the fake adapter. The adapter
    is the sole writer of ``sheet_projection_runs`` and
    ``sheet_bindings``; no Google Sheet is touched.

    The worker is the only authority that runs projections, mirroring
    the S1 durable-worker contract. A provider failure (env
    ``UGR_S5_GOOGLE_FAIL=1``) raises ``GoogleAdapterError`` and the row
    moves to ``FAILED`` with the structured ``last_error`` payload; the
    last-good projection in ``sheet_projection_runs`` is retained.
    """

    from ..connectors.google import GoogleAdapterError
    from ..domain.errors import DomainError
    from ..repositories.months import MonthRepository
    from ..services.google import GoogleProjectionService

    store_id = payload.get("store_id")
    month_id = payload.get("month_id")
    if not store_id or not month_id:
        raise DomainError(
            "store_id and month_id are required for GOOGLE_PROJECTION_STORE",
            details={"payload": payload},
        )
    month = MonthRepository(session).get(month_id)
    if month.tenant_id != tenant_id:
        raise DomainError(
            "tenant mismatch in GOOGLE_PROJECTION_STORE payload",
            details={
                "month_tenant": month.tenant_id,
                "job_tenant": tenant_id,
                "month_id": month_id,
            },
        )
    year = int(payload.get("year") or month.year)
    month_num = int(payload.get("month") or month.month)
    revision = int(payload.get("revision") or month.revision)
    service = GoogleProjectionService(session)
    outcome = service.project_store_for_month(
        tenant_id=tenant_id,
        store_id=store_id,
        month_id=month.id,
        year=year,
        month=month_num,
        revision=revision,
        generation=payload.get("generation"),
    )
    # Re-raise so the worker can mark the row FAILED; the last-good row
    # is preserved by the adapter.
    try:
        service.session.flush()
    except GoogleAdapterError:
        # The adapter raises after marking the FAILED row. Surface the
        # failure so the durable worker transitions the outbox row.
        raise
    return JobResult(
        status="DONE",
        payload={
            "tenant_id": tenant_id,
            "store_id": outcome.store_id,
            "generation": outcome.generation,
            "projection": {
                "store_id": outcome.projection.store_id,
                "generation": outcome.projection.generation,
                "last_success_generation": outcome.projection.last_success_generation,
            },
            "label": "google_projection_s5a",
        },
    )


def _jsonify(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


_REGISTRY: dict[str, JobHandler] = {
    JobKind.FIXTURE_INGEST: _job_fixture_ingest,
    JobKind.TENANT_BOOTSTRAP: _job_tenant_bootstrap,
    JobKind.NOOP: _job_noop,
    JobKind.EXPORT_XLSX_STORE: _job_export_xlsx_store,
    JobKind.EXPORT_XLSX_BULK: _job_export_xlsx_bulk,
    JobKind.EXPORT_PONTAJ_ONLY: _job_export_pontaj_only,
    JobKind.GOOGLE_PROJECTION_STORE: _job_google_projection_store,
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
