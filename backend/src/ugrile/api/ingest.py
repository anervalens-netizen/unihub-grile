"""Ingest endpoints.

S1 only exposes the fixture-v1 ingest path. Real Retail ingestion is owned
by Stage 7 and intentionally absent here — the import-boundary test enforces
that ``/opt/Mobiup/unihub-retail`` is not referenced anywhere in the source
tree.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import IngestRequest, IngestResult, JobOut
from ..connectors.fixtures import default_fixture
from ..connectors.ingest import FixtureConnector
from ..connectors.v1_types import (
    ConnectorHeader,
    PersonRecord,
    SalesRecord,
    StoreRecord,
)
from ..domain.enums import JobKind
from ..domain.errors import ConnectorError
from ..domain.identifiers import make_tenant_id
from ..services.auth import Principal, assert_admin
from ..worker.worker import enqueue, run_once

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/fixture", response_model=IngestResult)
def ingest_fixture(
    payload: IngestRequest = IngestRequest(),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> IngestResult:
    """Apply the built-in fixture.

    Two modes:

    * Inline (default): apply immediately in the request thread. Useful for
      smoke probes and integration tests.
    * Enqueue: write a row to ``outbox_jobs`` and let the worker process it.

    The fixture payload is deterministic; when ``tenant_token`` differs from
    the canonical fixture tenant, the API rewrites the header to the
    caller-provided token so the demo lands under the requested tenant.
    """

    assert_admin(principal)
    tenant_id = make_tenant_id(payload.tenant_token)

    if payload.enqueue:
        enqueue(
            session,
            tenant_id=tenant_id,
            kind=JobKind.FIXTURE_INGEST.value,
            idempotency_key=f"fixture:{tenant_id}",
        )
        session.commit()
        return IngestResult(
            tenant=tenant_id,
            generation="FIXTURE_V1",
            stores=0,
            people=0,
            sales=0,
        )

    try:
        connector = FixtureConnector(session)
        fixture = default_fixture()
        if fixture.header.tenant_id != tenant_id:
            # Rewrite every tenant_id (header + records) so the demo lands
            # under the caller's tenant. The store/person ids are derived
            # from stable internal codes and remain identical, so re-running
            # against the same tenant is idempotent.
            bare_tenant = (
                tenant_id[len("tenant_") :] if tenant_id.startswith("tenant_") else tenant_id
            )
            fixture = fixture.model_copy(
                update={
                    "header": ConnectorHeader.model_construct(
                        schema_name=fixture.header.schema_name,
                        generation=fixture.header.generation,
                        tenant_id=tenant_id,
                        emitted_at=fixture.header.emitted_at,
                    ),
                    "stores": [
                        StoreRecord(**{**r.model_dump(), "tenant_id": bare_tenant})
                        for r in fixture.stores
                    ],
                    "people": [
                        PersonRecord(**{**r.model_dump(), "tenant_id": bare_tenant})
                        for r in fixture.people
                    ],
                    "sales": [
                        SalesRecord(**{**r.model_dump(), "tenant_id": bare_tenant})
                        for r in fixture.sales
                    ],
                }
            )
        summary = connector.apply(fixture)
    except ConnectorError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    return IngestResult(
        tenant=str(summary["tenant"]),
        generation=str(summary["generation"]),
        stores=int(str(summary["stores"])),
        people=int(str(summary["people"])),
        sales=int(str(summary["sales"])),
    )


@router.post("/fixture/run", response_model=JobOut)
def ingest_fixture_run(
    payload: IngestRequest = IngestRequest(),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> JobOut:
    """Enqueue the fixture job, then run the worker once to settle it."""

    assert_admin(principal)
    tenant_id = make_tenant_id(payload.tenant_token)
    enqueue(
        session,
        tenant_id=tenant_id,
        kind=JobKind.FIXTURE_INGEST.value,
        idempotency_key=f"fixture:{tenant_id}:{int(time.time())}",
    )
    session.commit()
    row, _result = run_once(locked_by="ugrile-api")
    if row is None:
        raise HTTPException(status_code=500, detail={"code": "WORKER_EMPTY"})
    return JobOut.model_validate(row)


__all__ = ["router"]
