"""M3 realistic-scale query-count and latency contracts.

The dedicated CI step runs this file with ``-s`` so each measured sample is
recorded in the Actions log. Query-count ceilings are deterministic regression
budgets. Latency ceilings intentionally have wide CI headroom; the measured
numbers are evidence, while query counts are the primary anti-N+1 contract.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, sessionmaker

from tests.fixtures.performance import (
    PERF_PERSON_COUNT,
    PERF_STORE_COUNT,
    PerformanceDataset,
    seed_performance_dataset,
)
from ugrile.core import database

pytestmark = [pytest.mark.postgres, pytest.mark.performance]

# Initial ceilings are deliberately above the existing implementation while
# still small constants independent of 80-store / 160-person cardinality.
# After the first PostgreSQL CI sample these are tightened to measured headroom.
QUERY_BUDGETS = {
    "overview": 30,
    "program": 15,
    "grid": 45,
    "store_screen": 170,
}
LATENCY_BUDGET_MS = {
    "overview": 5000.0,
    "program": 6000.0,
    "grid": 2000.0,
    "store_screen": 12000.0,
}


@dataclass(frozen=True, slots=True)
class Measurement:
    label: str
    selects: int
    latency_ms: float


@contextmanager
def _postgres_api(pg_engine: Engine) -> Iterator[TestClient]:
    """Bind the application DB dependency to the disposable PostgreSQL engine."""

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


def _measure_gets(
    *,
    engine: Engine,
    client: TestClient,
    label: str,
    paths: Sequence[str],
    headers: dict[str, str],
) -> Measurement:
    selects = 0

    def _before_cursor_execute(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    started = perf_counter()
    try:
        for path in paths:
            response = client.get(path, headers=headers)
            assert response.status_code == 200, f"{path}: {response.text}"
    finally:
        elapsed_ms = (perf_counter() - started) * 1000
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)

    measurement = Measurement(label=label, selects=selects, latency_ms=elapsed_ms)
    print(
        f"M3_PERF {measurement.label} "
        f"selects={measurement.selects} latency_ms={measurement.latency_ms:.2f}"
    )
    return measurement


def _assert_budget(measurement: Measurement) -> None:
    assert measurement.selects <= QUERY_BUDGETS[measurement.label], (
        f"{measurement.label} issued {measurement.selects} SELECTs "
        f"(budget {QUERY_BUDGETS[measurement.label]})"
    )
    assert measurement.latency_ms <= LATENCY_BUDGET_MS[measurement.label], (
        f"{measurement.label} took {measurement.latency_ms:.2f}ms "
        f"(CI ceiling {LATENCY_BUDGET_MS[measurement.label]:.0f}ms)"
    )


def test_realistic_primary_read_performance(
    pg_engine: Engine,
    pg_session: Session,
) -> None:
    dataset: PerformanceDataset = seed_performance_dataset(pg_session)
    assert len(dataset.store_ids) == PERF_STORE_COUNT == 80
    assert len(dataset.person_ids) == PERF_PERSON_COUNT == 160

    month = dataset.month_id
    representative_store = dataset.store_ids[0]

    with _postgres_api(pg_engine) as client:
        # Warm framework/statement caches before timing. Warm-up is not measured.
        warmup = client.get(f"/months/{month}/overview", headers=dataset.headers)
        assert warmup.status_code == 200, warmup.text

        overview = _measure_gets(
            engine=pg_engine,
            client=client,
            label="overview",
            paths=[f"/months/{month}/overview"],
            headers=dataset.headers,
        )
        overview_body = client.get(
            f"/months/{month}/overview", headers=dataset.headers
        ).json()
        assert overview_body["kpis"]["stores_total"] == PERF_STORE_COUNT

        program = _measure_gets(
            engine=pg_engine,
            client=client,
            label="program",
            paths=[f"/months/{month}/program?perspective=stores"],
            headers=dataset.headers,
        )
        program_body = client.get(
            f"/months/{month}/program?perspective=stores", headers=dataset.headers
        ).json()
        assert len(program_body["rows"]) == PERF_STORE_COUNT

        grid = _measure_gets(
            engine=pg_engine,
            client=client,
            label="grid",
            paths=[f"/months/{month}/grid"],
            headers=dataset.headers,
        )
        grid_body = client.get(f"/months/{month}/grid", headers=dataset.headers).json()
        assert len(grid_body) == PERF_PERSON_COUNT

        # This mirrors the requests currently issued in parallel by Magazin.tsx.
        # Count them as one screen budget so future endpoint or frontend changes
        # cannot quietly introduce query fan-out across the composed screen.
        store_screen = _measure_gets(
            engine=pg_engine,
            client=client,
            label="store_screen",
            paths=[
                f"/months/{month}/program?perspective=people",
                f"/months/{month}/pontaj-totals",
                "/catalog/stores",
                f"/catalog/people?store_id={representative_store}",
                f"/months/{month}/attribution",
                f"/months/{month}/grid",
                f"/months/{month}/epay/freshness?store_id={representative_store}",
                f"/months/{month}/sheet-projection?store_id={representative_store}",
            ],
            headers=dataset.headers,
        )

    for measurement in (overview, program, grid, store_screen):
        _assert_budget(measurement)
