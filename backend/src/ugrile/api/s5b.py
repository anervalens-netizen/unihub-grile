"""S5b enqueue + canary endpoints (AC-12, AC-14, AC-16).

Routes:

* ``POST /months/{id}/export/store`` — admin-only. Body
  ``{"store_id": "...", "idempotency_key": "..."}``. Enqueues an
  ``EXPORT_XLSX_STORE`` durable worker job whose handler renders the
  per-magazin workbook, writes the artifact to a non-tracked path, and
  persists an ``ExportRun`` row with checksum + artifact_uri. The
  request returns the job id + status only; the bytes are produced
  asynchronously by the worker.

* ``POST /months/{id}/export/bulk`` — admin-only. Body
  ``{"store_ids": [...]?, "idempotency_key": "..."}``. Enqueues an
  ``EXPORT_XLSX_BULK`` job whose handler iterates per-store and packs
  the ZIP + manifest.json. Returns the job id only.

* ``POST /months/{id}/export/pontaj-only`` — admin-only. Body
  ``{"store_ids": [...]?, "idempotency_key": "..."}``. Enqueues an
  ``EXPORT_PONTAJ_ONLY`` job whose handler renders the Pontaj
  workbook for the requested stores.

* ``GET /months/{id}/export/jobs/{job_id}`` — admin or TL. Returns the
  current ``ExportRun`` summary + artifact_uri + status. Lets the
  client poll until the job is DONE.

* ``GET /months/{id}/canary/readback`` — admin or TL. Reads the latest
  DONE structural projection per store and returns per-store
  CanaryResult with missing/unexpected keys.

AC-16 contract: the API never renders the XLSX in the request
thread. All exports flow through the durable worker with SKIP LOCKED
+ idempotency_key, exposing progress state through the existing
``GET /worker/jobs/{id}`` endpoint.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..domain.enums import JobKind, RoleName
from ..domain.errors import DomainError, NotFoundError
from ..repositories.models import ExportRun
from ..repositories.months import MonthRepository
from ..services.auth import Principal, assert_admin, assert_same_tenant
from ..services.canary import list_active_store_ids, read_store_canary
from ..services.xlsx_export import create_pending_export_run
from ..worker.worker import enqueue

router = APIRouter(prefix="/months", tags=["export"])


def _artifact_uri_hint(filename: str) -> str:
    """Compute the durable artifact path the worker will write to.

    The worker uses the same helper so API and worker converge on the
    same path; the directory is created lazily by the worker.
    """
    base = os.environ.get("UGR_S5_EXPORT_DIR") or tempfile.gettempdir()
    return os.path.join(base, "ugrile-s5-exports", filename)


def _enqueue_export(
    session: Session,
    *,
    tenant_id: str,
    month_id: str,
    kind: JobKind,
    idempotency_key: str,
    payload: dict[str, Any],
    artifact_uri_hint: str | None,
) -> dict[str, Any]:
    """Enqueue an export job and return the synchronous ticket.

    Contract (AC-16): the ``job_id`` returned here is the
    ``ExportRun.id`` of a pre-created PENDING row. The durable worker
    updates that same row to ``DONE`` (or ``FAILED``) on completion, and
    the polling endpoint ``GET /months/{id}/export/jobs/{job_id}`` reads
    it by the same id. The internal ``OutboxJob`` row is implementation
    detail and not exposed to the client.
    """
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise DomainError(
            "idempotency_key is required",
            details={"code": "EXPORT_KEY_REQUIRED"},
        )
    export_run = create_pending_export_run(
        session,
        tenant_id=tenant_id,
        kind=kind.value,
        summary={"idempotency_key": idempotency_key, "payload": payload},
        artifact_uri_hint=artifact_uri_hint,
    )
    enqueue(
        session,
        tenant_id=tenant_id,
        kind=kind.value,
        idempotency_key=idempotency_key,
        payload={
            **payload,
            "month_id": month_id,
            "artifact_uri_hint": artifact_uri_hint,
            "export_run_id": export_run.id,
        },
    )
    job_id = export_run.id
    session.commit()
    return {
        "kind": kind.value,
        "month_id": month_id,
        "job_id": job_id,
        "idempotency_key": idempotency_key,
        "status": "ENQUEUED",
        "artifact_uri_hint": artifact_uri_hint,
    }


@router.post("/{month_id}/export/store")
def enqueue_export_store(
    month_id: str,
    body: dict[str, Any],
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    assert_admin(principal)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    store_id = body.get("store_id")
    if not isinstance(store_id, str) or not store_id:
        raise DomainError("store_id is required", details={"body_keys": list(body.keys())})
    idempotency_key = body.get("idempotency_key") or f"store::{store_id}::{month_id}"
    artifact_uri_hint = _artifact_uri_hint(
        f"ugrile_{month.year}-{month.month:02d}_{store_id[:8]}_grila_pontaj.xlsx"
    )
    return _enqueue_export(
        session,
        tenant_id=principal.tenant_id,
        month_id=month_id,
        kind=JobKind.EXPORT_XLSX_STORE,
        idempotency_key=idempotency_key,
        payload={"store_id": store_id},
        artifact_uri_hint=artifact_uri_hint,
    )


@router.post("/{month_id}/export/bulk")
def enqueue_export_bulk(
    month_id: str,
    body: dict[str, Any],
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    assert_admin(principal)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    store_ids = body.get("store_ids")
    if store_ids is not None and not isinstance(store_ids, list):
        raise DomainError("store_ids must be a list when provided", details={"type": type(store_ids).__name__})
    idempotency_key = body.get("idempotency_key") or f"bulk::{month_id}"
    artifact_uri_hint = _artifact_uri_hint(
        f"ugrile_{month.year}-{month.month:02d}_bulk.zip"
    )
    return _enqueue_export(
        session,
        tenant_id=principal.tenant_id,
        month_id=month_id,
        kind=JobKind.EXPORT_XLSX_BULK,
        idempotency_key=idempotency_key,
        payload={"store_ids": [str(s) for s in store_ids] if store_ids else None},
        artifact_uri_hint=artifact_uri_hint,
    )


@router.post("/{month_id}/export/pontaj-only")
def enqueue_export_pontaj_only(
    month_id: str,
    body: dict[str, Any],
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    assert_admin(principal)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    store_ids = body.get("store_ids")
    if store_ids is not None and not isinstance(store_ids, list):
        raise DomainError("store_ids must be a list when provided", details={"type": type(store_ids).__name__})
    idempotency_key = body.get("idempotency_key") or f"pontaj::{month_id}"
    artifact_uri_hint = _artifact_uri_hint(
        f"ugrile_{month.year}-{month.month:02d}_pontaj.xlsx"
    )
    return _enqueue_export(
        session,
        tenant_id=principal.tenant_id,
        month_id=month_id,
        kind=JobKind.EXPORT_PONTAJ_ONLY,
        idempotency_key=idempotency_key,
        payload={"store_ids": [str(s) for s in store_ids] if store_ids else None},
        artifact_uri_hint=artifact_uri_hint,
    )


@router.get("/{month_id}/export/jobs/{job_id}")
def export_job_status(
    month_id: str,
    job_id: int,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    if principal.role not in {RoleName.ADMIN, RoleName.MANAGER}:
        raise DomainError("forbidden", details={"role": principal.role.value})
    run = session.get(ExportRun, job_id)
    if run is None or run.tenant_id != principal.tenant_id:
        raise NotFoundError(
            "export job not found",
            details={"code": "EXPORT_JOB_NOT_FOUND", "job_id": job_id},
        )
    return {
        "job_id": run.id,
        "kind": run.kind,
        "status": run.status,
        "artifact_uri": run.artifact_uri,
        "summary": run.summary,
    }


@router.get("/{month_id}/export/jobs/{job_id}/download")
def download_export(
    month_id: str,
    job_id: int,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> FileResponse:
    """Download the persisted artifact for a completed export run.

    The client supplies only the stable ``ExportRun.id``. The filesystem
    path is always taken from the tenant-scoped row, never from a request
    parameter, and only a terminal DONE run can be served.
    """
    if principal.role not in {RoleName.ADMIN, RoleName.MANAGER}:
        raise DomainError("forbidden", details={"role": principal.role.value})
    run = session.get(ExportRun, job_id)
    if run is None or run.tenant_id != principal.tenant_id:
        raise NotFoundError(
            "export job not found",
            details={"code": "EXPORT_JOB_NOT_FOUND", "job_id": job_id},
        )
    if run.status != "DONE" or not run.artifact_uri:
        raise NotFoundError(
            "export artifact not found",
            details={"code": "EXPORT_ARTIFACT_NOT_FOUND", "job_id": job_id},
        )
    artifact = Path(run.artifact_uri)
    if not artifact.is_file():
        raise NotFoundError(
            "export artifact not found",
            details={"code": "EXPORT_ARTIFACT_NOT_FOUND", "job_id": job_id},
        )
    media_type = {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".zip": "application/zip",
    }.get(artifact.suffix.lower(), "application/octet-stream")
    return FileResponse(str(artifact), media_type=media_type, filename=artifact.name)


@router.get("/{month_id}/canary/readback")
def canary_readback(
    month_id: str,
    store_id: str | None = None,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    if principal.role not in {RoleName.ADMIN, RoleName.MANAGER}:
        raise DomainError("forbidden", details={"role": principal.role.value})
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    target_store_ids: list[str]
    if store_id is not None:
        target_store_ids = [store_id]
    else:
        target_store_ids = list_active_store_ids(session, tenant_id=principal.tenant_id)
    results = [
        asdict(read_store_canary(session, tenant_id=principal.tenant_id, store_id=s, month_id=month_id))
        for s in target_store_ids
    ]
    for r in results:
        r["ok"] = not r["missing_keys"] and not r["unexpected_keys"]
    return {"month_id": month_id, "results": results}


__all__ = ["router"]
