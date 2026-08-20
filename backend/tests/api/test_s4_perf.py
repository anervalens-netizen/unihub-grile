"""S4 perf benchmark — offline, no network, deterministic.

Asserts p95 < 500ms for the calendar cell save and p95 < 1s for the
overview / program / exceptions endpoints on the seeded fixture with at
least 75 stores and 31 days. The benchmark runs in-process so it has no
external dependencies and uses a real FastAPI TestClient against an
in-memory SQLite engine. The seeded store count is 80 (one over the
contract minimum) so the per-store fan-out check is meaningful.

The benchmark writes a raw numbers file under
``.agent/evidence/UGR-001/S4/perf.txt`` for the handoff.
"""

from __future__ import annotations

import os
import statistics
import time
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ugrile.connectors.fixtures import FIXTURE_TENANT_ID, default_fixture
from ugrile.connectors.ingest import FixtureConnector
from ugrile.core import database
from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.domain.identifiers import make_store_id
from ugrile.repositories.models import (
    Person,
    SiteDayAssignment,
    Store,
)
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService

HEADERS = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": FIXTURE_TENANT_ID}

OVERVIEW_BUDGET_MS = 1000.0  # AC-10 / AC-16 target for the seeded fixture
PROGRAM_BUDGET_MS = 1000.0
EXCEPTIONS_BUDGET_MS = 1000.0
SAVE_BUDGET_MS = 500.0


@pytest.fixture()
def client(engine, faker_tenant):
    from ugrile.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def _seed_month(*, month_id_out: list[str], faker_tenant: dict, store_count: int = 80) -> None:
    """Seed the canonical fixture plus extra stores and a 31-day calendar."""

    with database.session_scope() as session:
        connector = FixtureConnector(session)
        connector.apply(default_fixture())
        existing = session.query(Store).filter_by(tenant_id=faker_tenant["tenant_id"]).count()
        for offset in range(existing, store_count):
            session.add(
                Store(
                    id=make_store_id("acme", f"perf-{offset:03d}"),
                    tenant_id=faker_tenant["tenant_id"],
                    company_code="MOBIUP",
                    internal_code=f"perf-{offset:03d}",
                    name=f"Perf Store {offset:03d}",
                )
            )
            session.add(
                Person(
                    id=f"person_acme_perf_{offset:03d}",
                    tenant_id=faker_tenant["tenant_id"],
                    internal_code=f"perf-p-{offset:03d}",
                    display_name=f"Perf Person {offset:03d}",
                    home_store_id=make_store_id("acme", f"perf-{offset:03d}"),
                )
            )
        session.flush()
        month = MonthRepository(session).get_or_create(
            faker_tenant["tenant_id"], 2026, 8
        )
        month.state = MonthState.OPEN
        session.flush()
        # Build a 31-day calendar for the first 5 stores (the rest stay
        # uncovered so the overview has real blockers to detect).
        stores = (
            session.query(Store)
            .filter(Store.tenant_id == faker_tenant["tenant_id"])
            .all()
        )
        people = (
            session.query(Person)
            .filter(Person.tenant_id == faker_tenant["tenant_id"])
            .all()
        )
        for day in range(1, 32):
            store = stores[(day - 1) % 5]
            person = next(p for p in people if p.home_store_id == store.id)
            session.add(
                SiteDayAssignment(
                    tenant_id=faker_tenant["tenant_id"],
                    month_id=month.id,
                    store_id=store.id,
                    person_id=person.id,
                    business_date=date(2026, 8, day),
                    status=DayStatus.WORKING.value,
                    working_kind=WorkingKind.NORMAL.value,
                    revision=month.revision + 1,
                    source="PERF",
                )
            )
        session.commit()
        month_id_out.append(month.id)


def _percentile(samples: Iterable[float], fraction: float) -> float:
    values = sorted(samples)
    if not values:
        return 0.0
    idx = max(0, int(round(fraction * (len(values) - 1))))
    return values[idx]


def _write_perf_evidence(*, samples: dict[str, list[float]]) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    evidence_dir = repo_root / ".agent" / "evidence" / "UGR-001" / "S4"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    target = evidence_dir / "perf.txt"
    lines = []
    for label, runs in samples.items():
        if not runs:
            continue
        ms = [v * 1000 for v in runs]
        lines.append(
            f"{label}: n={len(ms)} mean={statistics.mean(ms):.1f}ms "
            f"p50={_percentile(ms, 0.5):.1f}ms p95={_percentile(ms, 0.95):.1f}ms "
            f"max={max(ms):.1f}ms"
        )
    target.write_text(
        "S4 perf benchmark — offline, no network\n"
        "Seeded fixture: 80 stores + 5-day calendar (31 days in the lattice)\n"
        "Thresholds: overview/program/exceptions p95 < 1000ms, save p95 < 500ms\n\n"
        + "\n".join(lines)
        + "\n",
        encoding="utf-8",
    )


def _time_call(callable_, *, iterations: int) -> list[float]:
    times: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        callable_()
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    return times


@pytest.mark.parametrize("iterations", [int(os.environ.get("UGR_S4_PERF_ITER", "12"))])
def test_s4_perf_overview_program_exceptions_save(client, faker_tenant, iterations):
    month_ids: list[str] = []
    _seed_month(month_id_out=month_ids, faker_tenant=faker_tenant)
    month_id = month_ids[0]

    def overview_call() -> None:
        client.get(f"/months/{month_id}/overview", headers=HEADERS)

    def program_call() -> None:
        client.get(
            f"/months/{month_id}/program?perspective=stores",
            headers=HEADERS,
        )

    def exceptions_call() -> None:
        client.get(f"/months/{month_id}/exceptions", headers=HEADERS)

    def save_call() -> None:
        # Edit the calendar cell at day 30 for the first person in scope.
        with database.session_scope() as session:
            people = (
                session.query(Person)
                .filter(Person.tenant_id == faker_tenant["tenant_id"])
                .limit(1)
                .all()
            )
            stores = (
                session.query(Store)
                .filter(Store.tenant_id == faker_tenant["tenant_id"])
                .limit(1)
                .all()
            )
            month = MonthRepository(session).get(month_id)
            CalendarService(session).apply(
                month=month,
                tenant_id=faker_tenant["tenant_id"],
                changes=[
                    CalendarChange(
                        person_id=people[0].id,
                        business_date=date(2026, 8, 30),
                        store_id=stores[0].id,
                        status=DayStatus.WORKING,
                        working_kind=WorkingKind.NORMAL,
                    )
                ],
                expected_revision=month.revision,
            )
            session.commit()

    overview_times = _time_call(overview_call, iterations=iterations)
    program_times = _time_call(program_call, iterations=iterations)
    exceptions_times = _time_call(exceptions_call, iterations=iterations)
    save_times = _time_call(save_call, iterations=iterations)

    samples = {
        "overview": overview_times,
        "program": program_times,
        "exceptions": exceptions_times,
        "save": save_times,
    }
    _write_perf_evidence(samples=samples)

    overview_p95 = _percentile(overview_times, 0.95) * 1000
    program_p95 = _percentile(program_times, 0.95) * 1000
    exceptions_p95 = _percentile(exceptions_times, 0.95) * 1000
    save_p95 = _percentile(save_times, 0.95) * 1000

    assert overview_p95 < OVERVIEW_BUDGET_MS, (
        f"overview p95 {overview_p95:.1f}ms > {OVERVIEW_BUDGET_MS:.1f}ms budget"
    )
    assert program_p95 < PROGRAM_BUDGET_MS, (
        f"program p95 {program_p95:.1f}ms > {PROGRAM_BUDGET_MS:.1f}ms budget"
    )
    assert exceptions_p95 < EXCEPTIONS_BUDGET_MS, (
        f"exceptions p95 {exceptions_p95:.1f}ms > {EXCEPTIONS_BUDGET_MS:.1f}ms budget"
    )
    assert save_p95 < SAVE_BUDGET_MS, (
        f"save p95 {save_p95:.1f}ms > {SAVE_BUDGET_MS:.1f}ms budget"
    )
