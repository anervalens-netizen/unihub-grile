"""Async export and canary endpoints.

Export/canary reads are authorized from persisted/requested store resources,
not merely from role + tenant. New export runs persist their month and store set
in the summary so later polling/download authorization can be replayed safely.

Export enqueue is idempotent on ``(tenant_id, kind, idempotency_key)``. An exact
replay returns the original ExportRun instead of creating a second job; reusing
the same key for a different logical request fails closed. Artifact targets are
operation-scoped so distinct exports cannot overwrite each other's history.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..domain.enums import JobKind, RoleName
from ..domain.errors import ConflictError, DomainError, NotFoundError, ScopeError
from ..repositories.models import ExportRun, OutboxJob
from ..repositories.months import MonthRepository
from ..services.auth import Principal, assert_same_tenant
from ..services.authorization import (
    Capability,
    authorize,
    authorize_store_for_month,
    authorize_store_set_for_month,
    month_store_ids,
)
from ..services.canary import list_active_store_ids, read_store_canary
from ..services.xlsx_export import create_pending_export_run
from ..worker.worker import enqueue

router = APIRouter(prefix="/months", tags=["export"])


def _artifact_uri_hint(
    *,
    tenant_id: str,
    kind: JobKind,
    idempotency_key: str,
    filename: str,
) -> str:
    """Return an operation-isolated deterministic artifact target.

    A replay of the same logical operation gets the same path. Different
    tenants, kinds, or idempotency keys never share a target, so a later export
    cannot silently mutate bytes behind an older ExportRun.
    """

    base = os.environ.get("UGR_S5_EXPORT_DIR") or tempfile.gettempdir()
    operation = f"{tenant_id}\0{kind.value}\0{idempotency_key}".encode()
    operation_id = hashlib.sha256(operation).hexdigest()[:20]
    return os.path.join(base, "ugrile-s5-exports", operation_id, filename)


def _summary_dict(run: ExportRun) -> dict[str, Any]:
    raw = run.summary
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _outbox_payload(row: OutboxJob) -> dict[str, Any]:
    try:
        decoded = json.loads(row.payload or "{}")
    except json.JSONDecodeError as exc:
        raise DomainError(
            "persisted export job payload is invalid",
            details={"code": "EXPORT_IDEMPOTENCY_STATE_INVALID", "outbox_job_id": row.id},
        ) from exc
    if not isinstance(decoded, dict):
        raise DomainError(
            "persisted export job payload is not an object",
            details={"code": "EXPORT_IDEMPOTENCY_STATE_INVALID", "outbox_job_id": row.id},
        )
    return decoded


def _existing_export_for_key(
    session: Session,
    *,
    tenant_id: str,
    month_id: str,
    kind: JobKind,
    idempotency_key: str,
    payload: dict[str, Any],
) -> ExportRun | None:
    """Resolve an exact idempotent replay or reject semantic key reuse."""

    row = (
        session.query(OutboxJob)
        .filter_by(
            tenant_id=tenant_id,
            kind=kind.value,
            idempotency_key=idempotency_key,
        )
        .one_or_none()
    )
    if row is None:
        return None

    persisted_payload = _outbox_payload(row)
    export_run_id = persisted_payload.get("export_run_id")
    if not isinstance(export_run_id, int) or export_run_id <= 0:
        raise DomainError(
            "persisted export idempotency state has no ExportRun",
            details={
                "code": "EXPORT_IDEMPOTENCY_STATE_INVALID",
                "outbox_job_id": row.id,
                "idempotency_key": idempotency_key,
            },
        )
    run = session.get(ExportRun, export_run_id)
    if run is None or run.tenant_id != tenant_id or run.kind != kind.value:
        raise DomainError(
            "persisted export idempotency state is inconsistent",
            details={
                "code": "EXPORT_IDEMPOTENCY_STATE_INVALID",
                "outbox_job_id": row.id,
                "export_run_id": export_run_id,
            },
        )

    summary = _summary_dict(run)
    if summary.get("month_id") != month_id or summary.get("payload") != payload:
        raise ConflictError(
            "idempotency key was already used for a different export request",
            details={
                "code": "EXPORT_IDEMPOTENCY_KEY_REUSED",
                "idempotency_key": idempotency_key,
                "existing_job_id": run.id,
            },
        )
    return run


def _export_response(
    run: ExportRun,
    *,
    month_id: str,
    idempotency_key: str,
    replayed: bool,
) -> dict[str, Any]:
    summary = _summary_dict(run)
    hint = summary.get("artifact_uri_hint")
    artifact_uri_hint = str(hint) if isinstance(hint, str) and hint else run.artifact_uri
    status = "ENQUEUED" if run.status == "PENDING" else run.status
    return {
        "kind": run.kind,
        "month_id": month_id,
        "job_id": run.id,
        "idempotency_key": idempotency_key,
        "status": status,
        "artifact_uri_hint": artifact_uri_hint,
        "replayed": replayed,
    }


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
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise DomainError(
            "idempotency_key is required",
            details={"code": "EXPORT_KEY_REQUIRED"},
        )

    existing = _existing_export_for_key(
        session,
        tenant_id=tenant_id,
        month_id=month_id,
        kind=kind,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    if existing is not None:
        return _export_response(
            existing,
            month_id=month_id,
            idempotency_key=idempotency_key,
            replayed=True,
        )

    try:
        export_run = create_pending_export_run(
            session,
            tenant_id=tenant_id,
            kind=kind.value,
            summary={
                "idempotency_key": idempotency_key,
                "month_id": month_id,
                "payload": payload,
            },
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
        session.commit()
    except IntegrityError:
        # A concurrent request can win the scoped uniqueness race after our
        # optimistic lookup. Roll back the losing ExportRun as well, then replay
        # the committed winner. If there is no exact winner, preserve the DB
        # failure instead of guessing.
        session.rollback()
        winner = _existing_export_for_key(
            session,
            tenant_id=tenant_id,
            month_id=month_id,
            kind=kind,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        if winner is None:
            raise
        return _export_response(
            winner,
            month_id=month_id,
            idempotency_key=idempotency_key,
            replayed=True,
        )

    return _export_response(
        export_run,
        month_id=month_id,
        idempotency_key=idempotency_key,
        replayed=False,
    )


def _run_store_ids(run: ExportRun, *, month_id: str) -> set[str] | None:
    """Return persisted resource scope or None when it cannot be proven.

    ``None`` is fail-closed for manager reads. Admins may still inspect legacy
    runs created before resource metadata became mandatory.
    """

    summary = _summary_dict(run)
    if summary.get("month_id") != month_id:
        return None
    payload = summary.get("payload")
    if not isinstance(payload, dict):
        return None
    store_id = payload.get("store_id")
    if isinstance(store_id, str) and store_id:
        return {store_id}
    store_ids = payload.get("store_ids")
    if isinstance(store_ids, list) and store_ids:
        return {str(value) for value in store_ids}
    # Empty/missing store_ids means tenant-wide bulk export; only an admin can
    # prove authorization for that resource set without a stored explicit set.
    return None


def _authorize_export_run(
    session: Session,
    principal: Principal,
    *,
    month_id: str,
    run: ExportRun,
) -> None:
    authorize(principal, Capability.EXPORT_READ)
    if run.tenant_id != principal.tenant_id:
        raise NotFoundError(
            "export job not found",
            details={"code": "EXPORT_JOB_NOT_FOUND", "job_id": run.id},
        )
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    if principal.role is RoleName.ADMIN:
        return
    store_ids = _run_store_ids(run, month_id=month_id)
    if not store_ids:
        raise ScopeError(
            "export resource scope cannot be proven for this principal",
            details={"job_id": run.id, "month_id": month_id},
        )
    authorize_store_set_for_month(
        session,
        principal,
        Capability.EXPORT_READ,
        month=month,
        store_ids=store_ids,
    )


@router.post("/{month_id}/export/store")
def enqueue_export_store(
    month_id: str,
    body: dict[str, Any],
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    authorize(principal, Capability.EXPORT_CREATE)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    store_id = body.get("store_id")
    if not isinstance(store_id, str) or not store_id:
        raise DomainError("store_id is required", details={"body_keys": list(body.keys())})
    authorize_store_for_month(
        session,
        principal,
        Capability.EXPORT_CREATE,
        month=month,
        store_id=store_id,
    )
    idempotency_key = body.get("idempotency_key") or f"store::{store_id}::{month_id}"
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise DomainError("idempotency_key is required", details={"code": "EXPORT_KEY_REQUIRED"})
    artifact_uri_hint = _artifact_uri_hint(
        tenant_id=principal.tenant_id,
        kind=JobKind.EXPORT_XLSX_STORE,
        idempotency_key=idempotency_key,
        filename=f"ugrile_{month.year}-{month.month:02d}_{store_id[:8]}_grila_pontaj.xlsx",
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
    authorize(principal, Capability.EXPORT_CREATE)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    store_ids = body.get("store_ids")
    if store_ids is not None and not isinstance(store_ids, list):
        raise DomainError(
            "store_ids must be a list when provided",
            details={"type": type(store_ids).__name__},
        )
    normalized = {str(s) for s in store_ids} if store_ids else set()
    if normalized:
        authorize_store_set_for_month(
            session,
            principal,
            Capability.EXPORT_CREATE,
            month=month,
            store_ids=normalized,
        )
    idempotency_key = body.get("idempotency_key") or f"bulk::{month_id}"
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise DomainError("idempotency_key is required", details={"code": "EXPORT_KEY_REQUIRED"})
    artifact_uri_hint = _artifact_uri_hint(
        tenant_id=principal.tenant_id,
        kind=JobKind.EXPORT_XLSX_BULK,
        idempotency_key=idempotency_key,
        filename=f"ugrile_{month.year}-{month.month:02d}_bulk.zip",
    )
    return _enqueue_export(
        session,
        tenant_id=principal.tenant_id,
        month_id=month_id,
        kind=JobKind.EXPORT_XLSX_BULK,
        idempotency_key=idempotency_key,
        payload={"store_ids": sorted(normalized) if normalized else None},
        artifact_uri_hint=artifact_uri_hint,
    )


@router.post("/{month_id}/export/pontaj-only")
def enqueue_export_pontaj_only(
    month_id: str,
    body: dict[str, Any],
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    authorize(principal, Capability.EXPORT_CREATE)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    store_ids = body.get("store_ids")
    if store_ids is not None and not isinstance(store_ids, list):
        raise DomainError(
            "store_ids must be a list when provided",
            details={"type": type(store_ids).__name__},
        )
    normalized = {str(s) for s in store_ids} if store_ids else set()
    if normalized:
        authorize_store_set_for_month(
            session,
            principal,
            Capability.EXPORT_CREATE,
            month=month,
            store_ids=normalized,
        )
    idempotency_key = body.get("idempotency_key") or f"pontaj::{month_id}"
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise DomainError("idempotency_key is required", details={"code": "EXPORT_KEY_REQUIRED"})
    artifact_uri_hint = _artifact_uri_hint(
        tenant_id=principal.tenant_id,
        kind=JobKind.EXPORT_PONTAJ_ONLY,
        idempotency_key=idempotency_key,
        filename=f"ugrile_{month.year}-{month.month:02d}_pontaj.xlsx",
    )
    return _enqueue_export(
        session,
        tenant_id=principal.tenant_id,
        month_id=month_id,
        kind=JobKind.EXPORT_PONTAJ_ONLY,
        idempotency_key=idempotency_key,
        payload={"store_ids": sorted(normalized) if normalized else None},
        artifact_uri_hint=artifact_uri_hint,
    )


@router.get("/{month_id}/export/jobs/{job_id}")
def export_job_status(
    month_id: str,
    job_id: int,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    run = session.get(ExportRun, job_id)
    if run is None:
        raise NotFoundError(
            "export job not found",
            details={"code": "EXPORT_JOB_NOT_FOUND", "job_id": job_id},
        )
    _authorize_export_run(session, principal, month_id=month_id, run=run)
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
    run = session.get(ExportRun, job_id)
    if run is None:
        raise NotFoundError(
            "export job not found",
            details={"code": "EXPORT_JOB_NOT_FOUND", "job_id": job_id},
        )
    _authorize_export_run(session, principal, month_id=month_id, run=run)
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
    authorize(principal, Capability.SHEET_READ)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    active_store_ids = set(list_active_store_ids(session, tenant_id=principal.tenant_id))
    visible_store_ids = month_store_ids(session, principal, month) & active_store_ids
    if store_id is not None:
        authorize_store_for_month(
            session,
            principal,
            Capability.SHEET_READ,
            month=month,
            store_id=store_id,
        )
        target_store_ids = [store_id]
    else:
        target_store_ids = sorted(visible_store_ids)
    results = [
        asdict(
            read_store_canary(
                session,
                tenant_id=principal.tenant_id,
                store_id=s,
                month_id=month_id,
            )
        )
        for s in target_store_ids
    ]
    for result in results:
        result["ok"] = not result["missing_keys"] and not result["unexpected_keys"]
    return {"month_id": month_id, "results": results}


__all__ = ["router"]
