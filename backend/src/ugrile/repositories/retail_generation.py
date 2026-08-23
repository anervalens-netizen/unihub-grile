"""Accepted Retail snapshot generation ledger over append-only ImportRun rows.

A successful Retail snapshot apply and its DONE ImportRun are committed in the
same caller transaction. The newest DONE run for a tenant/period is therefore
the accepted head; failed/rolled-back attempts cannot displace last-good state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.errors import ConnectorError
from .models import ImportRun

RETAIL_IMPORT_KIND = "RETAIL_SNAPSHOT_V1"


@dataclass(frozen=True, slots=True)
class AcceptedRetailGeneration:
    import_run_id: int
    tenant_id: str
    period: str
    generation_key: str
    snapshot_sha256: str
    schema_version: str
    sales_hash: str
    sales_revision: int
    campaign_revision: int
    cutoff_date: str


def _decode_run(row: ImportRun) -> AcceptedRetailGeneration | None:
    try:
        payload = json.loads(row.summary or "{}")
    except json.JSONDecodeError as exc:
        raise ConnectorError(
            "accepted Retail generation ledger contains invalid JSON",
            details={
                "code": "RETAIL_GENERATION_LEDGER_INVALID",
                "import_run_id": row.id,
            },
        ) from exc
    if not isinstance(payload, dict):
        raise ConnectorError(
            "accepted Retail generation ledger entry is not an object",
            details={
                "code": "RETAIL_GENERATION_LEDGER_INVALID",
                "import_run_id": row.id,
            },
        )
    required = {
        "period": str,
        "generation_key": str,
        "snapshot_sha256": str,
        "schema_version": str,
        "sales_hash": str,
        "sales_revision": int,
        "campaign_revision": int,
        "cutoff_date": str,
    }
    for key, expected_type in required.items():
        if not isinstance(payload.get(key), expected_type):
            raise ConnectorError(
                "accepted Retail generation ledger entry is incomplete",
                details={
                    "code": "RETAIL_GENERATION_LEDGER_INVALID",
                    "import_run_id": row.id,
                    "missing_or_invalid": key,
                },
            )
    return AcceptedRetailGeneration(
        import_run_id=row.id,
        tenant_id=row.tenant_id,
        period=str(payload["period"]),
        generation_key=str(payload["generation_key"]),
        snapshot_sha256=str(payload["snapshot_sha256"]),
        schema_version=str(payload["schema_version"]),
        sales_hash=str(payload["sales_hash"]),
        sales_revision=int(payload["sales_revision"]),
        campaign_revision=int(payload["campaign_revision"]),
        cutoff_date=str(payload["cutoff_date"]),
    )


def accepted_retail_generation(
    session: Session,
    *,
    tenant_id: str,
    period: str,
) -> AcceptedRetailGeneration | None:
    """Return the last committed accepted Retail generation for the period."""

    rows = list(
        session.execute(
            select(ImportRun)
            .where(
                ImportRun.tenant_id == tenant_id,
                ImportRun.kind == RETAIL_IMPORT_KIND,
                ImportRun.status == "DONE",
            )
            .order_by(ImportRun.id.desc())
        ).scalars()
    )
    for row in rows:
        decoded = _decode_run(row)
        if decoded is not None and decoded.period == period:
            return decoded
    return None


def accepted_retail_generation_key(
    session: Session,
    *,
    tenant_id: str,
    period: str,
) -> str | None:
    accepted = accepted_retail_generation(
        session,
        tenant_id=tenant_id,
        period=period,
    )
    return accepted.generation_key if accepted is not None else None


__all__ = [
    "AcceptedRetailGeneration",
    "RETAIL_IMPORT_KIND",
    "accepted_retail_generation",
    "accepted_retail_generation_key",
]
