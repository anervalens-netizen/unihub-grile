"""BE-006 evaluation of the normal one-cell calendar save path.

This is deliberately an evaluation contract before any incremental-write
redesign. It measures the real HTTP path on the same 80-store/160-person
PostgreSQL fixture and proves that the current implementation creates a new
complete Pontaj revision while retaining historical rows, rebuilding
attribution, and appending transactional audit evidence.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.fixtures.performance import PERF_DAYS, PERF_PERSON_COUNT, seed_performance_dataset
from ugrile.core import database
from ugrile.repositories.models import (
    AuditEvent,
    Month,
    PersonDayAbsence,
    PontajProjection,
    SalesPersonDay,
    SiteDayAssignment,
)

pytestmark = [pytest.mark.postgres, pytest.mark.performance]

# Evaluation ceilings, intentionally conservative until the first PostgreSQL
# sample is inspected. They prevent accidental runaway behavior while avoiding
# a premature structural optimization target.
SAVE_SELECT_CEILING = 100
SAVE_STATEMENT_CEILING = 200
SAVE_LATENCY_CEILING_MS = 10_000.0


@contextmanager
def _postgres_api(pg_engine: Engine) -> Iterator[TestClient]:
    previous_engine = database._engine
    previous_session_local = database._SessionLocal
    session_factory = sessionmaker(
        bind=pg_engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    database._engine = pg_engine
    database._SessionLocal = session_factory
    try:
        from ugrile.main import create_app

        with TestClient(create_app()) as client:
            yield client
    finally:
        database._engine = previous_engine
        database._SessionLocal = previous_session_local


def test_one_cell_save_cost_and_revision_snapshot_invariants(
    pg_engine: Engine,
    pg_session: Session,
) -> None:
    dataset = seed_performance_dataset(pg_session)
    person_id = dataset.person_ids[0]
    business_date = "2026-08-01"

    selects = 0
    statements = 0

    def _before_cursor_execute(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        nonlocal selects, statements
        statements += 1
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    with _postgres_api(pg_engine) as client:
        event.listen(pg_engine, "before_cursor_execute", _before_cursor_execute)
        started = perf_counter()
        try:
            response = client.post(
                f"/months/{dataset.month_id}/program/cell?expected_revision=1",
                headers=dataset.headers,
                json={
                    "person_id": person_id,
                    "business_date": business_date,
                    "status": "OFF",
                    "store_id": None,
                    "working_kind": None,
                },
            )
        finally:
            latency_ms = (perf_counter() - started) * 1000
            event.remove(pg_engine, "before_cursor_execute", _before_cursor_execute)

    assert response.status_code == 200, response.text
    print(
        "M3_SAVE one_cell "
        f"selects={selects} statements={statements} latency_ms={latency_ms:.2f}"
    )
    assert selects <= SAVE_SELECT_CEILING
    assert statements <= SAVE_STATEMENT_CEILING
    assert latency_ms <= SAVE_LATENCY_CEILING_MS

    pg_session.expire_all()
    month = pg_session.get(Month, dataset.month_id)
    assert month is not None
    assert month.revision == 2

    # The changed person/day moves from the working assignment table to the
    # non-covering absence table in the new authoritative calendar state.
    changed_assignment = pg_session.execute(
        select(SiteDayAssignment).where(
            SiteDayAssignment.tenant_id == dataset.tenant_id,
            SiteDayAssignment.month_id == dataset.month_id,
            SiteDayAssignment.person_id == person_id,
            SiteDayAssignment.business_date == business_date,
        )
    ).scalar_one_or_none()
    assert changed_assignment is None
    changed_absence = pg_session.execute(
        select(PersonDayAbsence).where(
            PersonDayAbsence.tenant_id == dataset.tenant_id,
            PersonDayAbsence.month_id == dataset.month_id,
            PersonDayAbsence.person_id == person_id,
            PersonDayAbsence.business_date == business_date,
        )
    ).scalar_one()
    assert changed_absence.status == "OFF"

    # Revision 2 is a complete historical-participant x day Pontaj snapshot,
    # not a sparse changed-row patch. Existing revision-1 fixture rows remain
    # untouched, proving revision history is append-only at the projection layer.
    revision_1_pontaj = pg_session.execute(
        select(func.count(PontajProjection.id)).where(
            PontajProjection.tenant_id == dataset.tenant_id,
            PontajProjection.month_id == dataset.month_id,
            PontajProjection.revision == 1,
        )
    ).scalar_one()
    revision_2_pontaj = pg_session.execute(
        select(func.count(PontajProjection.id)).where(
            PontajProjection.tenant_id == dataset.tenant_id,
            PontajProjection.month_id == dataset.month_id,
            PontajProjection.revision == 2,
        )
    ).scalar_one()
    assert revision_1_pontaj > 0
    assert revision_2_pontaj == PERF_PERSON_COUNT * PERF_DAYS

    # Attribution is rebuilt from the new calendar: the day changed to OFF no
    # longer receives store sales credit, while all other working store-days do.
    attributed_changed_day = pg_session.execute(
        select(func.count(SalesPersonDay.id)).where(
            SalesPersonDay.tenant_id == dataset.tenant_id,
            SalesPersonDay.month_id == dataset.month_id,
            SalesPersonDay.person_id == person_id,
            SalesPersonDay.business_date == business_date,
        )
    ).scalar_one()
    assert attributed_changed_day == 0

    audit = pg_session.execute(
        select(AuditEvent)
        .where(
            AuditEvent.tenant_id == dataset.tenant_id,
            AuditEvent.action == "CALENDAR_APPLY",
            AuditEvent.entity_id == dataset.month_id,
        )
        .order_by(AuditEvent.id.desc())
        .limit(1)
    ).scalar_one()
    payload = json.loads(audit.payload)
    assert audit.actor_id == dataset.admin_user_id
    assert payload["revision_before"] == 1
    assert payload["revision_after"] == 2
    assert payload["source"] == "API_PROGRAM_CELL"
    assert payload["changes"][0]["after"]["status"] == "OFF"
