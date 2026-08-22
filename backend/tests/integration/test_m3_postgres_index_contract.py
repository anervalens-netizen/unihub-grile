from __future__ import annotations

import pytest
from sqlalchemy import Engine, inspect

pytestmark = pytest.mark.postgres

EXPECTED: dict[str, dict[str, tuple[str, ...]]] = {
    "outbox_jobs": {
        "ix_outbox_pending_due": ("run_after", "id"),
        "ix_outbox_running_lease": ("locked_at", "id"),
        "ix_outbox_tenant_status_id": ("tenant_id", "status", "id"),
    },
    "grid_calculations": {
        "ix_grid_calculations_current_read": (
            "tenant_id",
            "month_id",
            "rule_pack_version",
            "revision",
            "store_id",
            "person_id",
        ),
    },
    "epay_observations": {
        "ix_epay_observations_latest_valid": (
            "tenant_id",
            "store_id",
            "person_id",
            "source",
            "observed_at",
            "id",
        ),
    },
    "sales_store_day": {
        "ix_sales_store_day_tenant_date": (
            "tenant_id",
            "business_date",
            "store_id",
            "generation",
        ),
    },
    "sheet_projection_runs": {
        "ix_sheet_projection_runs_store_latest": ("tenant_id", "store_id", "id"),
    },
}


def _index_map(engine: Engine, table: str) -> dict[str, dict[str, object]]:
    return {
        str(index["name"]): index
        for index in inspect(engine).get_indexes(table)
        if index.get("name") is not None
    }


def _where(index: dict[str, object]) -> str:
    dialect_options = index.get("dialect_options")
    if not isinstance(dialect_options, dict):
        return ""
    return str(dialect_options.get("postgresql_where", "")).lower()


def test_query_path_indexes_materialize_with_exact_column_order(pg_engine: Engine) -> None:
    for table, expected_indexes in EXPECTED.items():
        actual = _index_map(pg_engine, table)
        for name, expected_columns in expected_indexes.items():
            assert name in actual, (table, name, sorted(actual))
            assert tuple(actual[name]["column_names"]) == expected_columns


def test_queue_and_epay_indexes_keep_selective_partial_predicates(pg_engine: Engine) -> None:
    outbox = _index_map(pg_engine, "outbox_jobs")
    epay = _index_map(pg_engine, "epay_observations")

    assert "status = 'pending'" in _where(outbox["ix_outbox_pending_due"])
    assert "status = 'running'" in _where(outbox["ix_outbox_running_lease"])
    assert "is_valid is true" in _where(epay["ix_epay_observations_latest_valid"])
