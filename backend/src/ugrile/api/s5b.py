"""S5b export + canary endpoints (AC-12, AC-14).

Routes:

* ``POST /months/{id}/export/store`` — admin-only. Body
  ``{"store_id": "...", "idempotency_key": "..."}``. Renders the
  ``Grila``+``Pontaj`` workbook, persists an ``ExportRun`` row, writes
  the bytes to a non-tracked temporary path, and returns
  ``{filename, checksum_sha256, size_bytes, summary, artifact_uri,
  kind}``.
* ``POST /months/{id}/export/bulk`` — admin-only. Body
  ``{"store_ids": [...]?}``. Same envelope with a ``store_count`` and a
  ZIP artifact.
* ``POST /months/{id}/export/pontaj-only`` — admin-only. Renders a
  Pontaj-only workbook spanning every person in the tenant.
* ``GET /months/{id}/canary/readback`` — admin or TL. Reads the latest
  structural projection per store (fake adapter output) and returns
  per-store CanaryResult with missing/unexpected keys.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..domain.enums import JobKind, RoleName
from ..domain.errors import DomainError
from ..repositories.months import MonthRepository
from ..services.auth import Principal, assert_admin, assert_same_tenant
from ..services.canary import list_active_store_ids, read_store_canary
from ..services.xlsx_export import (
    record_export_run,
    render_bulk_export,
    render_pontaj_only_export,
    render_store_export,
)

router = APIRouter(prefix="/months", tags=["export"])


def _artifact_uri(filename: str) -> str:
    """Persist the bytes to a non-tracked temporary path and return its URI."""
    base = os.environ.get("UGR_S5_EXPORT_DIR") or tempfile.gettempdir()
    target = os.path.join(base, "ugrile-s5-exports", filename)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    return target


@router.post("/{month_id}/export/store")
def export_store(
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
    envelope = render_store_export(
        session, tenant_id=principal.tenant_id, month=month, store_id=store_id
    )
    uri = _artifact_uri(envelope.filename)
    with open(uri, "wb") as f:
        f.write(envelope.bytes_)
    record_export_run(
        session,
        tenant_id=principal.tenant_id,
        kind=JobKind.EXPORT_XLSX_STORE.value,
        envelope=envelope,
        artifact_uri=uri,
    )
    session.commit()
    return {
        "filename": envelope.filename,
        "checksum_sha256": envelope.checksum,
        "size_bytes": len(envelope.bytes_),
        "summary": envelope.summary,
        "artifact_uri": uri,
        "kind": JobKind.EXPORT_XLSX_STORE.value,
    }


@router.post("/{month_id}/export/bulk")
def export_bulk(
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
    envelope = render_bulk_export(
        session,
        tenant_id=principal.tenant_id,
        month=month,
        store_ids=[str(s) for s in store_ids] if store_ids else None,
    )
    uri = _artifact_uri(envelope.filename)
    with open(uri, "wb") as f:
        f.write(envelope.bytes_)
    record_export_run(
        session,
        tenant_id=principal.tenant_id,
        kind=JobKind.EXPORT_XLSX_BULK.value,
        envelope=envelope,
        artifact_uri=uri,
    )
    session.commit()
    return {
        "filename": envelope.filename,
        "checksum_sha256": envelope.checksum,
        "size_bytes": len(envelope.bytes_),
        "summary": envelope.summary,
        "artifact_uri": uri,
        "kind": JobKind.EXPORT_XLSX_BULK.value,
    }


@router.post("/{month_id}/export/pontaj-only")
def export_pontaj_only(
    month_id: str,
    body: dict[str, Any],
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    assert_admin(principal)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    envelope = render_pontaj_only_export(
        session, tenant_id=principal.tenant_id, month=month
    )
    uri = _artifact_uri(envelope.filename)
    with open(uri, "wb") as f:
        f.write(envelope.bytes_)
    record_export_run(
        session,
        tenant_id=principal.tenant_id,
        kind=JobKind.EXPORT_PONTAJ_ONLY.value,
        envelope=envelope,
        artifact_uri=uri,
    )
    session.commit()
    return {
        "filename": envelope.filename,
        "checksum_sha256": envelope.checksum,
        "size_bytes": len(envelope.bytes_),
        "summary": envelope.summary,
        "artifact_uri": uri,
        "kind": JobKind.EXPORT_PONTAJ_ONLY.value,
    }


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
