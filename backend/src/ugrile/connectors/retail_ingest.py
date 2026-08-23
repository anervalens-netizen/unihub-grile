"""Atomic apply of a validated RetailSnapshotV1 into existing Grile inputs.

The adapter/provider boundary is read-only. This service is Grile-owned and
translates a validated snapshot into the established connector persistence
contract. Data rows and the accepted-generation ImportRun are committed by the
caller as one transaction; a failed attempt cannot displace last-good state.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.errors import ConflictError, ConnectorError
from ..domain.identifiers import (
    make_person_id,
    make_store_id,
    tenant_slug_from_tenant_id,
)
from ..repositories.models import ImportRun, IncentiveInput, StoreTarget, Tenant
from ..repositories.retail_generation import (
    RETAIL_IMPORT_KIND,
    AcceptedRetailGeneration,
    accepted_retail_generation,
)
from .ingest import FixtureConnector
from .retail_adapter import RetailSnapshotAdapter
from .retail_contract_v1 import RetailSnapshotV1
from .v1_types import (
    ConnectorHeader,
    ConnectorV1Payload,
    IncentiveRecord,
    PersonRecord,
    SalesRecord,
    StoreRecord,
    TargetRecord,
)


def _canonical_snapshot_payload(snapshot: RetailSnapshotV1) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    generation = dict(payload["generation"])
    # Fetch time is diagnostic, not semantic source identity. Excluding it makes
    # a repeat fetch of the same accepted source data a true idempotent replay.
    generation.pop("generated_at", None)
    payload["generation"] = generation
    return payload


def snapshot_sha256(snapshot: RetailSnapshotV1) -> str:
    encoded = json.dumps(
        _canonical_snapshot_payload(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def snapshot_generation_key(snapshot: RetailSnapshotV1) -> str:
    """Return a DB-compatible generation key while the ledger retains full SHA."""

    return snapshot_sha256(snapshot)[:32]


def _stable_code(prefix: str, external_id: str) -> str:
    digest = hashlib.sha256(external_id.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _store_codes(snapshot: RetailSnapshotV1) -> dict[str, str]:
    return {
        row.external_store_id: _stable_code("rs", row.external_store_id)
        for row in snapshot.stores
    }


def _person_codes(snapshot: RetailSnapshotV1) -> dict[str, str]:
    return {
        row.external_person_id: _stable_code("rp", row.external_person_id)
        for row in snapshot.people
    }


def _next_target_version(
    session: Session,
    *,
    tenant_id: str,
    internal_store_code: str,
    year: int,
    month: int,
    kind: str,
) -> int:
    from ..repositories.models import Store

    store = session.execute(
        select(Store).where(
            Store.tenant_id == tenant_id,
            Store.internal_code == internal_store_code,
        )
    ).scalar_one_or_none()
    if store is None:
        return 1
    current = session.execute(
        select(func.max(StoreTarget.version)).where(
            StoreTarget.tenant_id == tenant_id,
            StoreTarget.store_id == store.id,
            StoreTarget.year == year,
            StoreTarget.month == month,
            StoreTarget.kind == kind,
        )
    ).scalar_one()
    return int(current or 0) + 1


def _next_incentive_version(
    session: Session,
    *,
    tenant_id: str,
    internal_person_code: str,
    year: int,
    month: int,
) -> int:
    from ..repositories.models import Person

    person = session.execute(
        select(Person).where(
            Person.tenant_id == tenant_id,
            Person.internal_code == internal_person_code,
        )
    ).scalar_one_or_none()
    if person is None:
        return 1
    current = session.execute(
        select(func.max(IncentiveInput.version)).where(
            IncentiveInput.tenant_id == tenant_id,
            IncentiveInput.person_id == person.id,
            IncentiveInput.year == year,
            IncentiveInput.month == month,
        )
    ).scalar_one()
    return int(current or 0) + 1


def _connector_payload(
    session: Session,
    snapshot: RetailSnapshotV1,
    *,
    generation_key: str,
) -> ConnectorV1Payload:
    store_codes = _store_codes(snapshot)
    person_codes = _person_codes(snapshot)
    stores = [
        StoreRecord(
            tenant_id=snapshot.tenant_id,
            internal_code=store_codes[row.external_store_id],
            external_code=row.external_store_id,
            company_code=row.company_code,
            name=row.display_name,
            is_active=row.is_active,
        )
        for row in snapshot.stores
    ]
    people = [
        PersonRecord(
            tenant_id=snapshot.tenant_id,
            internal_code=person_codes[row.external_person_id],
            external_code=row.external_person_id,
            home_store_internal_code=store_codes[row.home_store_external_id],
            display_name=row.display_name or row.external_person_id,
            is_active=row.is_active,
        )
        for row in snapshot.people
    ]
    sales = [
        SalesRecord(
            tenant_id=snapshot.tenant_id,
            store_internal_code=store_codes[row.external_store_id],
            business_date=row.business_date,
            amount=row.amount,
            currency=row.currency,
            source_ref=snapshot.generation.sales_hash,
            sim_quantity=row.sim_quantity or 0,
        )
        for row in snapshot.sales_store_day
    ]
    targets = [
        TargetRecord(
            tenant_id=snapshot.tenant_id,
            store_internal_code=store_codes[row.external_store_id],
            year=row.year,
            month=row.month,
            kind=row.kind,
            version=_next_target_version(
                session,
                tenant_id=snapshot.tenant_id,
                internal_store_code=store_codes[row.external_store_id],
                year=row.year,
                month=row.month,
                kind=row.kind,
            ),
            amount=row.amount,
            currency=row.currency,
            sales_days=row.sales_days,
        )
        for row in snapshot.targets
    ]
    incentives = [
        IncentiveRecord(
            tenant_id=snapshot.tenant_id,
            person_internal_code=person_codes[row.external_person_id],
            year=row.year,
            month=row.month,
            version=_next_incentive_version(
                session,
                tenant_id=snapshot.tenant_id,
                internal_person_code=person_codes[row.external_person_id],
                year=row.year,
                month=row.month,
            ),
            amount=row.amount,
            currency=row.currency,
        )
        for row in snapshot.incentives
    ]
    # ConnectorHeader's legacy validator is intentionally fixture-only. The
    # internal bridge is constructing an already validated M6 snapshot, so it
    # uses model_construct rather than weakening the historical public contract.
    header = ConnectorHeader.model_construct(
        schema_name="grile.connector.v1",
        generation=generation_key,
        tenant_id=snapshot.tenant_id,
        emitted_at=snapshot.generation.generated_at.isoformat(),
    )
    return ConnectorV1Payload.model_construct(
        header=header,
        stores=stores,
        people=people,
        sales=sales,
        targets=targets,
        incentives=incentives,
    )


def _target_version_ledger(
    snapshot: RetailSnapshotV1,
    payload: ConnectorV1Payload,
) -> list[dict[str, object]]:
    tenant_token = tenant_slug_from_tenant_id(snapshot.tenant_id)
    return [
        {
            "store_id": make_store_id(tenant_token, record.store_internal_code),
            "kind": record.kind,
            "version": record.version,
        }
        for record in payload.targets
    ]


def _incentive_version_ledger(
    snapshot: RetailSnapshotV1,
    payload: ConnectorV1Payload,
) -> list[dict[str, object]]:
    tenant_token = tenant_slug_from_tenant_id(snapshot.tenant_id)
    return [
        {
            "person_id": make_person_id(tenant_token, record.person_internal_code),
            "version": record.version,
        }
        for record in payload.incentives
    ]


def _validate_transition(
    accepted: AcceptedRetailGeneration | None,
    snapshot: RetailSnapshotV1,
    digest: str,
) -> bool:
    """Return True for an exact replay; reject source-head regression/conflict."""

    if accepted is None:
        return False
    if accepted.snapshot_sha256 == digest:
        return True
    incoming = snapshot.generation
    if incoming.sales_revision < accepted.sales_revision:
        raise ConflictError(
            "Retail sales generation is older than the accepted head",
            details={
                "code": "RETAIL_GENERATION_STALE",
                "accepted_sales_revision": accepted.sales_revision,
                "received_sales_revision": incoming.sales_revision,
            },
        )
    if incoming.campaign_revision < accepted.campaign_revision:
        raise ConflictError(
            "Retail campaign generation is older than the accepted head",
            details={
                "code": "RETAIL_GENERATION_STALE",
                "accepted_campaign_revision": accepted.campaign_revision,
                "received_campaign_revision": incoming.campaign_revision,
            },
        )
    if incoming.cutoff_date.isoformat() < accepted.cutoff_date:
        raise ConflictError(
            "Retail cutoff date is older than the accepted head",
            details={
                "code": "RETAIL_GENERATION_STALE",
                "accepted_cutoff_date": accepted.cutoff_date,
                "received_cutoff_date": incoming.cutoff_date.isoformat(),
            },
        )
    if (
        incoming.sales_revision == accepted.sales_revision
        and incoming.sales_hash != accepted.sales_hash
    ):
        raise ConflictError(
            "Retail sales hash changed without a revision advance",
            details={
                "code": "RETAIL_GENERATION_CONFLICT",
                "sales_revision": incoming.sales_revision,
            },
        )
    if (
        incoming.sales_revision == accepted.sales_revision
        and incoming.campaign_revision == accepted.campaign_revision
    ):
        raise ConflictError(
            "Retail snapshot changed without any generation revision advance",
            details={
                "code": "RETAIL_GENERATION_CONFLICT",
                "sales_revision": incoming.sales_revision,
                "campaign_revision": incoming.campaign_revision,
            },
        )
    return False


class RetailSnapshotIngestService:
    """Apply adapter snapshots without exposing provider details to domain code."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def apply_adapter(
        self,
        adapter: RetailSnapshotAdapter,
        *,
        tenant_id: str,
        period: str,
    ) -> dict[str, object]:
        snapshot = adapter.load_snapshot(tenant_id=tenant_id, period=period)
        return self.apply_snapshot(
            snapshot,
            expected_tenant_id=tenant_id,
            expected_period=period,
        )

    def apply_snapshot(
        self,
        snapshot: RetailSnapshotV1,
        *,
        expected_tenant_id: str,
        expected_period: str,
    ) -> dict[str, object]:
        if snapshot.tenant_id != expected_tenant_id:
            raise ConnectorError(
                "Retail snapshot tenant does not match requested tenant",
                details={
                    "code": "RETAIL_TENANT_MISMATCH",
                    "expected": expected_tenant_id,
                    "received": snapshot.tenant_id,
                },
            )
        if snapshot.period != expected_period:
            raise ConnectorError(
                "Retail snapshot period does not match requested period",
                details={
                    "code": "RETAIL_PERIOD_MISMATCH",
                    "expected": expected_period,
                    "received": snapshot.period,
                },
            )
        if snapshot.manager_scopes:
            # Manager keys cannot become User foreign keys before INT-012
            # defines the identity translation. Ignoring them would silently
            # weaken effective authorization, so fail closed instead.
            raise ConnectorError(
                "Retail manager scopes require the identity adapter contract",
                details={"code": "RETAIL_MANAGER_IDENTITY_UNRESOLVED"},
            )
        if snapshot.payroll_inputs:
            raise ConnectorError(
                "Retail payroll input kinds require an explicit Grile mapping",
                details={"code": "RETAIL_PAYROLL_INPUT_MAPPING_REQUIRED"},
            )

        digest = snapshot_sha256(snapshot)
        generation_key = digest[:32]

        # A tenant-row lock serializes accepted-head transitions. This avoids a
        # stale writer validating against head N, waiting behind head N+1, then
        # incorrectly publishing N again. It also serializes target/incentive
        # version allocation for the tenant.
        with self.session.begin_nested():
            tenant = self.session.get(Tenant, snapshot.tenant_id)
            if tenant is None:
                tenant = Tenant(
                    id=snapshot.tenant_id,
                    name=tenant_slug_from_tenant_id(snapshot.tenant_id)
                    .replace("_", " ")
                    .title(),
                    timezone=snapshot.timezone,
                    is_active=True,
                )
                self.session.add(tenant)
                self.session.flush()
            tenant = self.session.execute(
                select(Tenant)
                .where(Tenant.id == snapshot.tenant_id)
                .with_for_update()
            ).scalar_one()
            if tenant.timezone != snapshot.timezone:
                raise ConnectorError(
                    "Retail snapshot timezone conflicts with the Grile tenant",
                    details={
                        "code": "RETAIL_TIMEZONE_MISMATCH",
                        "tenant_id": snapshot.tenant_id,
                        "expected": tenant.timezone,
                        "received": snapshot.timezone,
                    },
                )

            accepted = accepted_retail_generation(
                self.session,
                tenant_id=snapshot.tenant_id,
                period=snapshot.period,
            )
            if _validate_transition(accepted, snapshot, digest):
                assert accepted is not None
                return {
                    "status": "REPLAYED",
                    "tenant_id": snapshot.tenant_id,
                    "period": snapshot.period,
                    "generation_key": accepted.generation_key,
                    "snapshot_sha256": accepted.snapshot_sha256,
                    "import_run_id": accepted.import_run_id,
                }

            connector_payload = _connector_payload(
                self.session,
                snapshot,
                generation_key=generation_key,
            )
            applied = FixtureConnector(self.session).apply(connector_payload)
            ledger = {
                "schema_version": snapshot.schema_version,
                "period": snapshot.period,
                "generation_key": generation_key,
                "snapshot_sha256": digest,
                "sales_hash": snapshot.generation.sales_hash,
                "sales_revision": snapshot.generation.sales_revision,
                "campaign_revision": snapshot.generation.campaign_revision,
                "cutoff_date": snapshot.generation.cutoff_date.isoformat(),
                "target_versions": _target_version_ledger(snapshot, connector_payload),
                "incentive_versions": _incentive_version_ledger(
                    snapshot,
                    connector_payload,
                ),
                "counts": {
                    "stores": len(snapshot.stores),
                    "people": len(snapshot.people),
                    "sales_store_day": len(snapshot.sales_store_day),
                    "targets": len(snapshot.targets),
                    "incentives": len(snapshot.incentives),
                },
            }
            row = ImportRun(
                tenant_id=snapshot.tenant_id,
                kind=RETAIL_IMPORT_KIND,
                status="DONE",
                summary=json.dumps(ledger, sort_keys=True, separators=(",", ":")),
                errors="[]",
            )
            self.session.add(row)
            self.session.flush()

            return {
                "status": "ACCEPTED",
                "tenant_id": snapshot.tenant_id,
                "period": snapshot.period,
                "generation_key": generation_key,
                "snapshot_sha256": digest,
                "import_run_id": row.id,
                "applied": applied,
            }


__all__ = [
    "RetailSnapshotIngestService",
    "snapshot_generation_key",
    "snapshot_sha256",
]
