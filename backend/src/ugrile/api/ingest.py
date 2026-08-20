"""Ingest endpoints.

S1 only exposes the fixture-v1 ingest path. Real Retail ingestion is owned
by Stage 7 and intentionally absent here — the import-boundary test enforces
that ``/opt/Mobiup/unihub-retail`` is not referenced anywhere in the source
tree.

Single worker authority
-----------------------

The contract is explicit: the durable worker is the **only** authority
that executes jobs. The API layer is therefore restricted to **enqueue**.
``POST /ingest/fixture`` writes a row to ``outbox_jobs`` and returns; the
worker started via ``python -m ugrile.worker.worker`` settles it
asynchronously.

The endpoint also ensures the caller tenant exists before enqueueing —
otherwise the ``outbox_jobs.tenant_id`` foreign key would reject the
insert for a brand-new tenant.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import IngestRequest, IngestResult
from ..domain.enums import JobKind
from ..domain.identifiers import make_tenant_id, tenant_slug_from_tenant_id
from ..repositories.tenants import TenantRepository
from ..services.auth import Principal, assert_admin
from ..worker.worker import enqueue

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/fixture", response_model=IngestResult)
def enqueue_fixture(
    payload: IngestRequest = IngestRequest(),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> IngestResult:
    """Enqueue the fixture job for the caller tenant.

    The handler applies the canonical v1 fixture rewritten to land under
    ``make_tenant_id(payload.tenant_token)`` (defaults to ``fixture``). The
    job payload carries the explicit override so the worker honours the
    caller request, even when the caller principal belongs to a different
    tenant.
    """

    assert_admin(principal)
    tenant_id = make_tenant_id(payload.tenant_token)

    # Ensure the target tenant row exists before enqueuing so the
    # ``outbox_jobs.tenant_id`` foreign key does not reject the insert.
    # The flush is explicit so the tenant row is committed before the
    # outbox insert hits PostgreSQL's deferred FK check.
    token = tenant_slug_from_tenant_id(tenant_id)
    TenantRepository(session).upsert(
        tenant_id=tenant_id,
        name=token.replace("_", " ").title() or tenant_id,
        timezone="Europe/Bucharest",
    )
    session.flush()
    # The idempotency key includes a per-call timestamp so the same caller
    # can re-enqueue the fixture after the previous job settles. The
    # fixture itself is idempotent at the data layer (the connector
    # upserts by composite key), so re-running produces no duplicates.
    idempotency_key = f"fixture:{tenant_id}:{int(time.time() * 1000)}"
    enqueue(
        session,
        tenant_id=tenant_id,
        kind=JobKind.FIXTURE_INGEST.value,
        idempotency_key=idempotency_key,
        payload={"tenant_token": payload.tenant_token},
    )
    session.commit()
    return IngestResult(
        tenant=tenant_id,
        generation="FIXTURE_V1",
        stores=0,
        people=0,
        sales=0,
    )


__all__ = ["router"]
