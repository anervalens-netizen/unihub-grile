"""E-pay readback and Sheet projection endpoints.

All store-facing operations pass through the central capability/resource-scope
boundary. Tenant equality alone is not sufficient authorization.

Sheet projection enqueue is revision-bound, binding-bound and idempotent.
Replays of the same logical projection return the existing durable job, while
reuse of a key for a different month/store/revision/binding fails closed.
"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import (
    EpayFreshnessOut,
    EpayReadbackIn,
    EpayReadbackItemOut,
    EpayReadbackOut,
    JobOut,
    SheetProjectionEnqueueIn,
    SheetProjectionOut,
    SheetProjectionPayloadOut,
)
from ..connectors.google import (
    GoogleAdapterError,
    fake_spreadsheet_id,
    read_store_projection,
)
from ..connectors.google import last_error as google_last_error
from ..connectors.google import last_run_at as google_last_run_at
from ..core.config import get_settings
from ..domain.enums import JobKind
from ..domain.errors import ConflictError, DomainError
from ..repositories.models import Month, OutboxJob, SheetProjectionRun
from ..repositories.months import MonthRepository
from ..services.auth import Principal, assert_same_tenant
from ..services.authorization import Capability, authorize, authorize_store_for_month
from ..services.epay import freshness_for_month, record_readback
from ..services.month_write_gate import lock_month_for_financial_write
from ..services.sheet_bindings import get_sheet_binding
from ..services.snapshot_revision import calendar_data_revision
from ..worker.worker import enqueue

router = APIRouter(prefix="/months", tags=["epay-sheets"])


def _month_for(session: Session, month_id: str, principal: Principal) -> Month:
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    return month


def _job_payload(row: OutboxJob) -> dict[str, object]:
    try:
        decoded = json.loads(row.payload or "{}")
    except json.JSONDecodeError as exc:
        raise DomainError(
            "persisted sheet projection job payload is invalid",
            details={"code": "SHEET_IDEMPOTENCY_STATE_INVALID", "job_id": row.id},
        ) from exc
    if not isinstance(decoded, dict):
        raise DomainError(
            "persisted sheet projection job payload is not an object",
            details={"code": "SHEET_IDEMPOTENCY_STATE_INVALID", "job_id": row.id},
        )
    return dict(decoded)


def _projection_semantic_payload(payload: dict[str, object]) -> dict[str, object]:
    return {
        key: payload.get(key)
        for key in (
            "month_id",
            "store_id",
            "year",
            "month",
            "month_revision",
            "revision",
            "binding_spreadsheet_id",
            "binding_sheet_name_grila",
            "binding_sheet_name_pontaj",
        )
    }


def _existing_projection_job(
    session: Session,
    *,
    tenant_id: str,
    idempotency_key: str,
    payload: dict[str, object],
) -> OutboxJob | None:
    row = (
        session.query(OutboxJob)
        .filter_by(
            tenant_id=tenant_id,
            kind=JobKind.GOOGLE_PROJECTION_STORE.value,
            idempotency_key=idempotency_key,
        )
        .one_or_none()
    )
    if row is None:
        return None
    if _projection_semantic_payload(_job_payload(row)) != _projection_semantic_payload(payload):
        raise ConflictError(
            "idempotency key was already used for a different sheet projection request",
            details={
                "code": "SHEET_IDEMPOTENCY_KEY_REUSED",
                "idempotency_key": idempotency_key,
                "existing_job_id": row.id,
            },
        )
    return row


def _projection_binding_identity(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
) -> tuple[str, str, str]:
    """Resolve the complete Sheet identity to pin into a durable projection job."""

    binding = get_sheet_binding(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
    )
    if binding is not None:
        return (
            binding.spreadsheet_id,
            binding.sheet_name_grila,
            binding.sheet_name_pontaj,
        )
    if get_settings().google_provider == "fake":
        return (fake_spreadsheet_id(tenant_id, store_id), "Grila", "Pontaj")
    raise ConflictError(
        "live Google projection requires an explicit store Sheet binding",
        details={"code": "SHEET_BINDING_REQUIRED", "store_id": store_id},
    )


def _default_projection_idempotency_key(
    *,
    month_id: str,
    store_id: str,
    month_revision: int,
    data_revision: int,
    binding_identity: tuple[str, str, str],
) -> str:
    material = "\0".join(
        (
            month_id,
            store_id,
            str(month_revision),
            str(data_revision),
            *binding_identity,
        )
    ).encode()
    digest = hashlib.sha256(material).hexdigest()[:32]
    return (
        f"{JobKind.GOOGLE_PROJECTION_STORE.value}:{digest}:"
        f"m{month_revision}:d{data_revision}"
    )


@router.post("/{month_id}/epay/readback", response_model=EpayReadbackOut)
def post_epay_readback(
    month_id: str,
    payload: EpayReadbackIn,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> EpayReadbackOut:
    """Persist E-pay close input only while the month is writable."""

    authorize(principal, Capability.EPAY_WRITE)
    month = lock_month_for_financial_write(
        session,
        tenant_id=principal.tenant_id,
        month_id=month_id,
    )
    authorize_store_for_month(
        session,
        principal,
        Capability.EPAY_WRITE,
        month=month,
        store_id=payload.store_id,
    )
    try:
        result = record_readback(
            session,
            tenant_id=principal.tenant_id,
            month_id=month.id,
            store_id=payload.store_id,
            observations=[entry.model_dump() for entry in payload.observations],
            actor_id=principal.user_id,
        )
    except DomainError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    session.commit()
    return EpayReadbackOut(
        store_id=result.store_id,
        month_id=result.month_id,
        observed_at=result.observed_at.isoformat(),
        valid_count=result.valid_count,
        invalid_count=result.invalid_count,
        items=[
            EpayReadbackItemOut(
                person_id=item.person_id,
                category=item.category,
                value=item.value,
                raw_value=item.raw_value,
                is_valid=item.is_valid,
            )
            for item in result.items
        ],
    )


@router.get("/{month_id}/epay/freshness", response_model=EpayFreshnessOut)
def get_epay_freshness(
    month_id: str,
    store_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> EpayFreshnessOut:
    month = _month_for(session, month_id, principal)
    authorize_store_for_month(
        session,
        principal,
        Capability.EPAY_READ,
        month=month,
        store_id=store_id,
    )
    report = freshness_for_month(
        session,
        tenant_id=principal.tenant_id,
        store_id=store_id,
        month_id=month.id,
    )
    return EpayFreshnessOut(
        store_id=store_id,
        is_fresh=report.is_fresh,
        fresh_count=report.fresh_count,
        expected_count=report.expected_count,
        threshold=report.threshold.isoformat(),
    )


@router.get("/{month_id}/sheet-projection", response_model=SheetProjectionOut)
def get_sheet_projection(
    month_id: str,
    store_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> SheetProjectionOut:
    month = _month_for(session, month_id, principal)
    authorize_store_for_month(
        session,
        principal,
        Capability.SHEET_READ,
        month=month,
        store_id=store_id,
    )
    try:
        projection = read_store_projection(
            session,
            tenant_id=principal.tenant_id,
            store_id=store_id,
        )
    except GoogleAdapterError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    last_run = google_last_run_at(session, tenant_id=principal.tenant_id, store_id=store_id)
    last_error = google_last_error(session, tenant_id=principal.tenant_id, store_id=store_id)
    failures_value = 0
    if projection is not None:
        run = (
            session.query(SheetProjectionRun)
            .filter_by(
                tenant_id=principal.tenant_id,
                store_id=store_id,
                generation=projection.generation,
            )
            .order_by(SheetProjectionRun.id.desc())
            .first()
        )
        if run is not None:
            failures_value = int(run.failures or 0)
    response_payload: SheetProjectionPayloadOut | None = None
    if projection is not None:
        response_payload = SheetProjectionPayloadOut(
            grila=dict(projection.grila),
            pontaj=dict(projection.pontaj),
        )
    last_success = projection.generation if projection is not None else None
    return SheetProjectionOut(
        store_id=store_id,
        generation=projection.generation if projection is not None else "",
        last_success_generation=last_success,
        last_run_at=last_run.isoformat() if last_run else None,
        last_error=last_error,
        failures=failures_value,
        payload=response_payload,
    )


@router.post("/{month_id}/sheet-projection/enqueue", response_model=JobOut)
def post_sheet_projection_enqueue(
    month_id: str,
    payload: SheetProjectionEnqueueIn,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> JobOut:
    month = _month_for(session, month_id, principal)
    authorize_store_for_month(
        session,
        principal,
        Capability.SHEET_SYNC,
        month=month,
        store_id=payload.store_id,
    )
    data_revision = calendar_data_revision(
        session,
        tenant_id=principal.tenant_id,
        month_id=month.id,
        fallback_revision=month.revision,
    )
    binding_identity = _projection_binding_identity(
        session,
        tenant_id=principal.tenant_id,
        store_id=payload.store_id,
    )
    idempotency_key = payload.idempotency_key or _default_projection_idempotency_key(
        month_id=month.id,
        store_id=payload.store_id,
        month_revision=month.revision,
        data_revision=data_revision,
        binding_identity=binding_identity,
    )
    job_payload: dict[str, object] = {
        "month_id": month.id,
        "store_id": payload.store_id,
        "year": month.year,
        "month": month.month,
        "month_revision": month.revision,
        "revision": data_revision,
        "binding_spreadsheet_id": binding_identity[0],
        "binding_sheet_name_grila": binding_identity[1],
        "binding_sheet_name_pontaj": binding_identity[2],
        "enqueued_by": principal.user_id,
    }
    existing = _existing_projection_job(
        session,
        tenant_id=principal.tenant_id,
        idempotency_key=idempotency_key,
        payload=job_payload,
    )
    if existing is not None:
        return JobOut.model_validate(existing)

    try:
        row = enqueue(
            session,
            tenant_id=principal.tenant_id,
            kind=JobKind.GOOGLE_PROJECTION_STORE.value,
            idempotency_key=idempotency_key,
            payload=job_payload,
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        winner = _existing_projection_job(
            session,
            tenant_id=principal.tenant_id,
            idempotency_key=idempotency_key,
            payload=job_payload,
        )
        if winner is None:
            raise
        return JobOut.model_validate(winner)
    return JobOut.model_validate(row)


__all__ = ["router"]
