"""Calendar write/read endpoints (S1 minimal).

Only the endpoints required by the S1 contract live here:

* POST /months/{month_id}/assignments — upsert a working assignment.
* GET /months/{month_id}/coverage — return any AC-02 conflicts.
* GET /months/{month_id}/assignments — list the working assignments.

Wider manager scope (mid-month re-write, EXTRA_HOME classification, OFF/LEAVE
projection) is part of S2 and intentionally omitted.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import (
    AssignmentCreate,
    AssignmentOut,
    ConflictOut,
    CoverageReport,
    MonthOut,
)
from ..domain.errors import (
    CoverageInvariantError,
    ValidationError,
)
from ..repositories.assignments import AssignmentRepository
from ..repositories.models import SiteDayAssignment
from ..repositories.months import MonthRepository
from ..repositories.stores import PersonRepository, StoreRepository
from ..services.auth import Principal, assert_manager_or_admin, assert_same_tenant

router = APIRouter(prefix="/months", tags=["calendar"])


def _rehydrate(row: SiteDayAssignment) -> AssignmentOut:
    return AssignmentOut.model_validate(row)


@router.get("", response_model=list[MonthOut])
def list_months(
    tenant_id: str | None = None,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[MonthOut]:
    if tenant_id is not None:
        assert_same_tenant(principal, tenant_id)
    else:
        tenant_id = principal.tenant_id
    return [MonthOut.model_validate(m) for m in MonthRepository(session).list_for_tenant(tenant_id)]


@router.post("/{month_id}/assignments", response_model=AssignmentOut)
def upsert_assignment(
    month_id: str,
    payload: AssignmentCreate,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> AssignmentOut:
    """Upsert one working assignment. Conflicts raise HTTP 409."""

    assert_manager_or_admin(principal)
    months = MonthRepository(session)
    month = months.get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    if month.state == "CLOSED":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MONTH_CLOSED",
                "message": f"month is closed: {month_id}",
            },
        )
    if payload.expected_revision is not None and payload.expected_revision != month.revision:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_REVISION",
                "message": "expected revision does not match current revision",
                "expected": payload.expected_revision,
                "current": month.revision,
            },
        )

    stores = StoreRepository(session)
    people = PersonRepository(session)
    store = stores.get(payload.store_id)
    person = people.get(payload.person_id)

    assert_same_tenant(principal, store.tenant_id)
    assert_same_tenant(principal, person.tenant_id)
    if store.tenant_id != month.tenant_id or person.tenant_id != month.tenant_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TENANT_MISMATCH",
                "message": "store/person/month belong to different tenants",
            },
        )

    # Working-kind preconditions (EXTRA_HOME / EXTRA_OTHER).
    try:
        from ..domain.calendar import validate_working_kind

        validate_working_kind(
            person_home_store_id=person.home_store_id,
            site_store_id=store.id,
            working_kind=payload.working_kind,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc

    repo = AssignmentRepository(session)
    try:
        row = repo.upsert_working(
            tenant_id=month.tenant_id,
            month_id=month.id,
            store_id=store.id,
            person_id=person.id,
            business_date=payload.business_date,
            working_kind=payload.working_kind,
        )
    except CoverageInvariantError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc

    # Bump the month revision after a successful write so subsequent CAS
    # requests detect the drift.
    months.bump_revision(month.id)
    return _rehydrate(row)


@router.get("/{month_id}/assignments", response_model=list[AssignmentOut])
def list_assignments(
    month_id: str,
    store_id: str | None = None,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[AssignmentOut]:
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    rows = AssignmentRepository(session).list_for_month(month_id, store_id=store_id)
    return [_rehydrate(r) for r in rows]


@router.get("/{month_id}/coverage", response_model=CoverageReport)
def month_coverage(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> CoverageReport:
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    rows = AssignmentRepository(session).list_for_month(month_id)
    projections = AssignmentRepository(session).project_assignments(rows)
    from ..domain.calendar import check_coverage

    conflicts = check_coverage(projections)
    return CoverageReport(
        month_id=month.id,
        conflicts=[
            ConflictOut(
                code=c.code,
                message=c.message,
                store_id=c.store_id,
                person_id=c.person_id,
                business_date=c.business_date.isoformat() if c.business_date else None,
            )
            for c in conflicts
        ],
    )


__all__ = ["router"]
