from __future__ import annotations

import importlib.util
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ugrile.domain.close import MonthCloseEventRecord, verify_month_close_chain
from ugrile.repositories.models import Month, MonthCloseEvent, Tenant


def _load_repair_migration():
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0013_repair_close_chain_partitioning.py"
    )
    spec = importlib.util.spec_from_file_location("repair_close_chain_partitioning", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repair_close_chain_partitions_interleaved_months(pg_engine) -> None:
    tenant_id = "tenant_pre_server_chain"
    month_a_id = "month_pre_server_2026_07"
    month_b_id = "month_pre_server_2026_08"
    base = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)

    with Session(pg_engine) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name="Pre-server migration test",
                timezone="Europe/Bucharest",
            )
        )
        session.add_all(
            [
                Month(
                    id=month_a_id, tenant_id=tenant_id, year=2026, month=7,
                    state="CLOSED", revision=2,
                ),
                Month(
                    id=month_b_id, tenant_id=tenant_id, year=2026, month=8,
                    state="CLOSED", revision=2,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                MonthCloseEvent(
                    tenant_id=tenant_id, month_id=month_a_id, action="CLOSE",
                    previous_state="OPEN", new_state="CLOSED", revision_before=0,
                    revision_after=1, actor_id="user_a", reason=None, blockers="[]",
                    previous_event_digest="f" * 64, event_digest="a" * 64,
                    occurred_at=base,
                ),
                MonthCloseEvent(
                    tenant_id=tenant_id, month_id=month_b_id, action="CLOSE",
                    previous_state="OPEN", new_state="CLOSED", revision_before=0,
                    revision_after=1, actor_id="user_b", reason=None, blockers="[]",
                    previous_event_digest="a" * 64, event_digest="b" * 64,
                    occurred_at=base + timedelta(seconds=1),
                ),
                MonthCloseEvent(
                    tenant_id=tenant_id, month_id=month_a_id, action="REOPEN",
                    previous_state="CLOSED", new_state="REOPENED", revision_before=1,
                    revision_after=2, actor_id="user_a", reason="audit repair",
                    blockers="[]", previous_event_digest="b" * 64,
                    event_digest="c" * 64, occurred_at=base + timedelta(seconds=2),
                ),
                MonthCloseEvent(
                    tenant_id=tenant_id, month_id=month_b_id, action="REOPEN",
                    previous_state="CLOSED", new_state="REOPENED", revision_before=1,
                    revision_after=2, actor_id="user_b", reason="audit repair",
                    blockers="[]", previous_event_digest="c" * 64,
                    event_digest="d" * 64, occurred_at=base + timedelta(seconds=3),
                ),
            ]
        )
        session.commit()

    migration = _load_repair_migration()
    with pg_engine.begin() as conn:
        migration._repair_close_chains(conn)

    with Session(pg_engine) as session:
        rows = list(
            session.execute(
                select(MonthCloseEvent).order_by(
                    MonthCloseEvent.month_id,
                    MonthCloseEvent.occurred_at,
                    MonthCloseEvent.id,
                )
            ).scalars()
        )

    grouped: dict[str, list[MonthCloseEventRecord]] = defaultdict(list)
    for row in rows:
        grouped[row.month_id].append(
            MonthCloseEventRecord(
                id=row.id, tenant_id=row.tenant_id, month_id=row.month_id,
                action=row.action, previous_state=row.previous_state,
                new_state=row.new_state, revision_before=row.revision_before,
                revision_after=row.revision_after, actor_id=row.actor_id,
                reason=row.reason, blockers=row.blockers,
                previous_event_digest=row.previous_event_digest,
                event_digest=row.event_digest,
            )
        )

    assert set(grouped) == {month_a_id, month_b_id}
    for records in grouped.values():
        assert records[0].previous_event_digest is None
        assert verify_month_close_chain(records) == []
