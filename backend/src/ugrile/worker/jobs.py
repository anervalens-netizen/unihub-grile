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

Revision-bound side effects
---------------------------

Exports and Sheet projections pin both ``month_revision`` and the
calendar-derived data revision when they are enqueued. The worker locks the
in-tenant Month row and revalidates both identities before any side effect.
That lock remains held for the handler transaction, so calendar edits,
close/reopen, rendering and projection publication cannot interleave into a
mixed snapshot. Obsolete jobs fail terminally instead of silently rendering a
newer revision.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
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
from ..repositories.models import ExportRun, ImportRun, Month
from ..services.snapshot_revision import calendar_data_revision


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
    """Atomically publish bytes without exposing a partial target file."""
    import os
    import tempfile

    directory = os.path.dirname(uri)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".ugrile-artifact-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, uri)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _resolve_export_run_id(
    payload: dict[str, Any],
    *,
    kind: JobKind,
) -> int:
    """Read the pre-created ``export_run_id`` from the job payload.

    The API enqueue path always sets this; missing here means a stale or
    hand-crafted job that bypassed the contract.
    """

    raw = payload.get("export_run_id")
    if not isinstance(raw, int) or raw <= 0:
        raise DomainError(
            f"{kind.value} payload is missing export_run_id; the API "
            "enqueue contract is the only path that may dispatch this kind",
            details={"payload_keys": sorted(payload.keys())},
        )
    return raw


def _mark_export_run_failed(
    session: Session,
    *,
    export_run_id: int,
    tenant_id: str,
    exc: BaseException,
) -> None:
    """Best-effort transition of the ExportRun to FAILED.

    The error string is intentionally compact — the worker also writes
    a more detailed reason on the OutboxJob row, which remains the
    authoritative job log.
    """

    from ..services.xlsx_export import finalize_export_run

    error_kind = exc.__class__.__name__
    message = str(exc) or repr(exc)
    finalize_export_run(
        session,
        export_run_id=export_run_id,
        tenant_id=tenant_id,
        status="FAILED",
        errors=f"{error_kind}: {message}",
    )


def _resolve_artifact_uri(payload: dict[str, Any], fallback_filename: str) -> str:
    """Pick the durable artifact URI the worker should write to."""

    artifact_uri = payload.get("artifact_uri_hint") or payload.get("artifact_uri")
    if artifact_uri:
        return str(artifact_uri)
    import os
    import tempfile

    base = os.environ.get("UGR_S5_EXPORT_DIR") or tempfile.gettempdir()
    return os.path.join(base, "ugrile-s5-exports", fallback_filename)


def _required_revision(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int or value < 0:
        raise DomainError(
            "revision-bound job is missing valid revision metadata",
            details={
                "code": "JOB_REVISION_METADATA_MISSING",
                "revision_key": key,
                "value": value,
            },
        )
    return value


def _lock_revision_bound_month(
    session: Session,
    *,
    tenant_id: str,
    payload: dict[str, Any],
    data_revision_key: str,
) -> tuple[Month, int]:
    """Lock and attest the exact month/data snapshot requested by a job."""

    month_id = payload.get("month_id")
    if not isinstance(month_id, str) or not month_id:
        raise DomainError(
            "revision-bound job is missing month_id",
            details={"code": "JOB_MONTH_ID_MISSING"},
        )
    expected_month_revision = _required_revision(payload, "month_revision")
    expected_data_revision = _required_revision(payload, data_revision_key)
    month = session.execute(
        select(Month)
        .where(Month.id == month_id, Month.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if month is None:
        raise DomainError(
            "revision-bound job month does not exist in tenant",
            details={"code": "JOB_MONTH_NOT_FOUND", "month_id": month_id},
        )
    if month.revision != expected_month_revision:
        raise DomainError(
            "revision-bound job is obsolete because the month revision advanced",
            details={
                "code": "JOB_MONTH_REVISION_STALE",
                "month_id": month.id,
                "expected": expected_month_revision,
                "current": month.revision,
            },
        )
    current_data_revision = calendar_data_revision(
        session,
        tenant_id=tenant_id,
        month_id=month.id,
        fallback_revision=month.revision,
    )
    if current_data_revision != expected_data_revision:
        raise DomainError(
            "revision-bound job is obsolete because the data revision advanced",
            details={
                "code": "JOB_DATA_REVISION_STALE",
                "month_id": month.id,
                "expected": expected_data_revision,
                "current": current_data_revision,
            },
        )
    return month, expected_data_revision


def _job_export_xlsx_store(session: Session, tenant_id: str, payload: dict[str, Any]) -> JobResult:
    """Render one store export only from the revision attested at enqueue."""

    from ..services.revisioned_xlsx_export import render_store_export_at_revision
    from ..services.xlsx_export import finalize_export_run

    store_id = payload.get("store_id")
    if not isinstance(store_id, str) or not store_id:
        raise DomainError("store_id is required", details={"payload": payload})
    export_run_id = _resolve_export_run_id(payload, kind=JobKind.EXPORT_XLSX_STORE)
    try:
        month, data_revision = _lock_revision_bound_month(
            session,
            tenant_id=tenant_id,
            payload=payload,
            data_revision_key="data_revision",
        )
        envelope = render_store_export_at_revision(
            session,
            tenant_id=tenant_id,
            month=month,
            store_id=store_id,
            revision=data_revision,
        )
        artifact_uri = _resolve_artifact_uri(payload, envelope.filename)
        _write_bytes(artifact_uri, envelope.bytes_)
    except Exception as exc:
        _mark_export_run_failed(
            session,
            export_run_id=export_run_id,
            tenant_id=tenant_id,
            exc=exc,
        )
        raise
    finalize_export_run(
        session,
        export_run_id=export_run_id,
        tenant_id=tenant_id,
        status="DONE",
        artifact_uri=artifact_uri,
        summary={
            "filename": envelope.filename,
            "checksum_sha256": envelope.checksum,
            "summary": envelope.summary,
        },
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
    """Render a bulk export only from the revision attested at enqueue."""

    from ..services.revisioned_xlsx_export import render_bulk_export_at_revision
    from ..services.xlsx_export import finalize_export_run

    export_run_id = _resolve_export_run_id(payload, kind=JobKind.EXPORT_XLSX_BULK)
    try:
        month, data_revision = _lock_revision_bound_month(
            session,
            tenant_id=tenant_id,
            payload=payload,
            data_revision_key="data_revision",
        )
        store_ids = payload.get("store_ids")
        envelope = render_bulk_export_at_revision(
            session,
            tenant_id=tenant_id,
            month=month,
            store_ids=[str(s) for s in store_ids] if store_ids else None,
            revision=data_revision,
        )
        artifact_uri = _resolve_artifact_uri(payload, envelope.filename)
        _write_bytes(artifact_uri, envelope.bytes_)
    except Exception as exc:
        _mark_export_run_failed(
            session,
            export_run_id=export_run_id,
            tenant_id=tenant_id,
            exc=exc,
        )
        raise
    finalize_export_run(
        session,
        export_run_id=export_run_id,
        tenant_id=tenant_id,
        status="DONE",
        artifact_uri=artifact_uri,
        summary={
            "filename": envelope.filename,
            "checksum_sha256": envelope.checksum,
            "summary": envelope.summary,
        },
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
    """Render Pontaj only from the revision attested at enqueue."""

    from ..services.revisioned_xlsx_export import render_pontaj_only_export_at_revision
    from ..services.xlsx_export import finalize_export_run

    export_run_id = _resolve_export_run_id(payload, kind=JobKind.EXPORT_PONTAJ_ONLY)
    try:
        month, data_revision = _lock_revision_bound_month(
            session,
            tenant_id=tenant_id,
            payload=payload,
            data_revision_key="data_revision",
        )
        store_ids = payload.get("store_ids")
        envelope = render_pontaj_only_export_at_revision(
            session,
            tenant_id=tenant_id,
            month=month,
            store_ids=[str(s) for s in store_ids] if store_ids else None,
            revision=data_revision,
        )
        artifact_uri = _resolve_artifact_uri(payload, envelope.filename)
        _write_bytes(artifact_uri, envelope.bytes_)
    except Exception as exc:
        _mark_export_run_failed(
            session,
            export_run_id=export_run_id,
            tenant_id=tenant_id,
            exc=exc,
        )
        raise
    finalize_export_run(
        session,
        export_run_id=export_run_id,
        tenant_id=tenant_id,
        status="DONE",
        artifact_uri=artifact_uri,
        summary={
            "filename": envelope.filename,
            "checksum_sha256": envelope.checksum,
            "summary": envelope.summary,
        },
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
    """Project only the calendar snapshot attested when the job was queued."""

    from ..connectors.google import GoogleAdapterError
    from ..services.google import GoogleProjectionService

    store_id = payload.get("store_id")
    if not isinstance(store_id, str) or not store_id:
        raise DomainError(
            "store_id is required for GOOGLE_PROJECTION_STORE",
            details={"payload": payload},
        )
    month, data_revision = _lock_revision_bound_month(
        session,
        tenant_id=tenant_id,
        payload=payload,
        data_revision_key="revision",
    )
    year = payload.get("year")
    month_num = payload.get("month")
    if type(year) is not int or type(month_num) is not int:
        raise DomainError(
            "projection job is missing valid year/month metadata",
            details={"code": "JOB_PERIOD_METADATA_MISSING"},
        )
    if year != month.year or month_num != month.month:
        raise DomainError(
            "projection job period metadata does not match the locked month",
            details={
                "code": "JOB_PERIOD_METADATA_MISMATCH",
                "month_id": month.id,
                "expected_year": month.year,
                "expected_month": month.month,
                "payload_year": year,
                "payload_month": month_num,
            },
        )
    service = GoogleProjectionService(session)
    outcome = service.project_store_for_month(
        tenant_id=tenant_id,
        store_id=store_id,
        month_id=month.id,
        year=year,
        month=month_num,
        revision=data_revision,
        generation=payload.get("generation"),
    )
    # Re-raise so the worker can mark the row FAILED; the last-good row
    # is preserved by the adapter.
    try:
        service.session.flush()
    except GoogleAdapterError:
        raise
    return JobResult(
        status="DONE",
        payload={
            "tenant_id": tenant_id,
            "store_id": outcome.store_id,
            "generation": outcome.generation,
            "revision": data_revision,
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
