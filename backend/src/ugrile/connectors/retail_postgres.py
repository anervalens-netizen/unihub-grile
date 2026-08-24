"""Read-only adapter for the existing UniHub Retail reporting database."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..domain.errors import ConnectorError
from .retail_contract_v1 import (
    RetailGenerationV1,
    RetailIncentiveV1,
    RetailPersonV1,
    RetailSalesStoreDayV1,
    RetailSnapshotV1,
    RetailStoreV1,
    RetailTargetV1,
)

_SPACE_RE = re.compile(r"\s+")


def normalize_agent_name(value: str) -> str:
    return _SPACE_RE.sub(" ", value.strip()).casefold()


def _psycopg_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql://", 1).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


class PostgresRetailSnapshotAdapter:
    """Build a complete Grile snapshot using only Retail reporting tables."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _psycopg_url(database_url)
        self.observed_assignments: list[tuple[str, date, str]] = []

    def load_snapshot(self, *, tenant_id: str, period: str) -> RetailSnapshotV1:
        with psycopg.connect(  # noqa: SIM117 - keeps connection/cursor lifetimes explicit
            self.database_url,
            options="-c default_transaction_read_only=on -c statement_timeout=30000",
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                read_only_row = cursor.fetchone()
                if read_only_row is None or read_only_row["transaction_read_only"] != "on":
                    raise ConnectorError(
                        "Retail connection is not read-only",
                        details={"code": "RETAIL_READ_ONLY_REQUIRED"},
                    )

                cursor.execute(
                    "SELECT cutoff_date FROM reporting_sales_cutoff_v1 "
                    "WHERE import_month = %s",
                    (period,),
                )
                cutoff_row = cursor.fetchone()
                if cutoff_row is None or not isinstance(cutoff_row["cutoff_date"], date):
                    raise ConnectorError(
                        "Retail reporting cutoff is unavailable",
                        details={"code": "RETAIL_CUTOFF_UNAVAILABLE", "period": period},
                    )
                cutoff = cutoff_row["cutoff_date"]

                cursor.execute(
                    """
                    SELECT s.site_code, s.locatie, s.firma, s.regional, s.asm, s.is_active
                    FROM stores s
                    WHERE s.is_active
                      AND (
                        EXISTS (SELECT 1 FROM reporting_agent_day r
                                WHERE r.import_month = %s AND r.site_code = s.site_code)
                        OR EXISTS (SELECT 1 FROM store_targets t
                                   WHERE t.import_month = %s AND t.site_code = s.site_code)
                      )
                    ORDER BY s.asm, s.locatie, s.site_code
                    """,
                    (period, period),
                )
                store_rows = list(cursor.fetchall())
                store_ids = {str(row["site_code"]) for row in store_rows}

                cursor.execute(
                    """
                    SELECT site_code, sale_date, SUM(total_sales) AS amount
                    FROM reporting_agent_day
                    WHERE import_month = %s AND sale_date <= %s
                    GROUP BY site_code, sale_date
                    ORDER BY site_code, sale_date
                    """,
                    (period, cutoff),
                )
                sales_rows = [
                    row for row in cursor.fetchall() if str(row["site_code"]) in store_ids
                ]

                cursor.execute(
                    """
                    SELECT site_code, agent, COUNT(DISTINCT sale_date) AS active_days,
                           SUM(total_sales) AS amount
                    FROM reporting_agent_day
                    WHERE import_month = %s AND sale_date <= %s
                    GROUP BY site_code, agent
                    ORDER BY site_code, agent
                    """,
                    (period, cutoff),
                )
                activity_rows = [
                    row for row in cursor.fetchall() if str(row["site_code"]) in store_ids
                ]

                cursor.execute(
                    """
                    SELECT site_code, sale_date, agent, SUM(total_sales) AS amount
                    FROM reporting_agent_day
                    WHERE import_month = %s AND sale_date <= %s
                    GROUP BY site_code, sale_date, agent
                    ORDER BY sale_date, amount DESC, site_code, agent
                    """,
                    (period, cutoff),
                )
                daily_activity_rows = [
                    row for row in cursor.fetchall() if str(row["site_code"]) in store_ids
                ]

                cursor.execute(
                    """
                    WITH latest AS (
                      SELECT MAX(import_month) AS import_month
                      FROM agent_targets WHERE import_month <= %s
                    )
                    SELECT site_code, agent FROM agent_targets, latest
                    WHERE agent_targets.import_month = latest.import_month
                    """,
                    (period,),
                )
                target_home = {
                    normalize_agent_name(str(row["agent"])): str(row["site_code"])
                    for row in cursor.fetchall()
                    if str(row["site_code"]) in store_ids
                }

                cursor.execute(
                    "SELECT site_code, target_value FROM store_targets "
                    "WHERE import_month = %s ORDER BY site_code",
                    (period,),
                )
                target_rows = [
                    row for row in cursor.fetchall() if str(row["site_code"]) in store_ids
                ]

                cursor.execute(
                    """
                    SELECT agent, COALESCE(SUM(incentive_value), 0) AS amount
                    FROM reporting_campaign_month_v3
                    WHERE period = %s
                    GROUP BY agent
                    """,
                    (period,),
                )
                incentive_by_agent = {
                    normalize_agent_name(str(row["agent"])): Decimal(str(row["amount"]))
                    for row in cursor.fetchall()
                }

                cursor.execute("SELECT COALESCE(MAX(revision), 0) AS revision FROM retail_outbox_events")
                revision_row = cursor.fetchone()
                if revision_row is None:
                    raise ConnectorError(
                        "Retail source revision is unavailable",
                        details={"code": "RETAIL_REVISION_UNAVAILABLE"},
                    )
                revision = int(revision_row["revision"])

        stores = [
            RetailStoreV1(
                external_store_id=str(row["site_code"]),
                display_name=str(row["locatie"]),
                company_code=str(row["firma"]),
                regional_key=str(row["regional"]),
                manager_key=str(row["asm"]),
                is_active=bool(row["is_active"]),
            )
            for row in store_rows
        ]

        activity_by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
        display_by_agent: dict[str, str] = {}
        for row in activity_rows:
            key = normalize_agent_name(str(row["agent"]))
            display_by_agent.setdefault(key, _SPACE_RE.sub(" ", str(row["agent"]).strip()))
            activity_by_agent[key].append(row)

        people: list[RetailPersonV1] = []
        for agent_key, rows in sorted(activity_by_agent.items()):
            ranked = sorted(
                rows,
                key=lambda row: (
                    -int(row["active_days"]),
                    -Decimal(str(row["amount"])),
                    str(row["site_code"]),
                ),
            )
            candidate = target_home.get(agent_key, str(ranked[0]["site_code"]))
            if candidate not in store_ids:
                candidate = str(ranked[0]["site_code"])
            people.append(
                RetailPersonV1(
                    external_person_id=agent_key,
                    display_name=display_by_agent[agent_key],
                    home_store_external_id=candidate,
                )
            )

        # Retail has observed sales activity, not an authoritative schedule.
        # Seed a conflict-free editable calendar from that evidence: at most one
        # agent per store/day and at most one store per agent/day.
        selected_stores: set[tuple[date, str]] = set()
        selected_people: set[tuple[date, str]] = set()
        observed: list[tuple[str, date, str]] = []
        for row in sorted(
            daily_activity_rows,
            key=lambda item: (
                item["sale_date"],
                -Decimal(str(item["amount"])),
                str(item["site_code"]),
                normalize_agent_name(str(item["agent"])),
            ),
        ):
            store_key = (row["sale_date"], str(row["site_code"]))
            person_key = (row["sale_date"], normalize_agent_name(str(row["agent"])))
            if store_key in selected_stores or person_key in selected_people:
                continue
            selected_stores.add(store_key)
            selected_people.add(person_key)
            observed.append((str(row["site_code"]), row["sale_date"], person_key[1]))
        self.observed_assignments = observed

        year, month = (int(part) for part in period.split("-", 1))
        sales = [
            RetailSalesStoreDayV1(
                external_store_id=str(row["site_code"]),
                business_date=row["sale_date"],
                amount=Decimal(str(row["amount"])),
            )
            for row in sales_rows
        ]
        targets = [
            RetailTargetV1(
                external_store_id=str(row["site_code"]),
                year=year,
                month=month,
                kind="MONTHLY_SALES",
                amount=Decimal(str(row["target_value"])),
                sales_days=26,
            )
            for row in target_rows
        ]
        incentives = [
            RetailIncentiveV1(
                external_person_id=person.external_person_id,
                year=year,
                month=month,
                amount=incentive_by_agent.get(person.external_person_id, Decimal("0")),
                authority_status="retail-reporting",
            )
            for person in people
        ]

        semantic = {
            "period": period,
            "cutoff": cutoff.isoformat(),
            "stores": [row.model_dump(mode="json") for row in stores],
            "people": [row.model_dump(mode="json") for row in people],
            "sales": [row.model_dump(mode="json") for row in sales],
            "targets": [row.model_dump(mode="json") for row in targets],
            "incentives": [row.model_dump(mode="json") for row in incentives],
        }
        sales_hash = hashlib.sha256(
            json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return RetailSnapshotV1(
            tenant_id=tenant_id,
            timezone="Europe/Bucharest",
            period=period,
            generation=RetailGenerationV1(
                sales_hash=sales_hash,
                sales_revision=revision,
                campaign_revision=revision,
                cutoff_date=cutoff,
                generated_at=datetime.now(UTC),
            ),
            stores=stores,
            people=people,
            sales_store_day=sales,
            targets=targets,
            incentives=incentives,
        )


__all__ = ["PostgresRetailSnapshotAdapter", "normalize_agent_name"]
