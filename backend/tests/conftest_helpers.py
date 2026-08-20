"""Shared test helpers."""

from __future__ import annotations

from ugrile.connectors.fixtures import default_fixture
from ugrile.connectors.v1_types import (
    ConnectorHeader,
    IncentiveRecord,
    PersonRecord,
    SalesRecord,
    StoreRecord,
    TargetRecord,
)


def fixture_for_tenant(tenant_id: str):
    """Return the canonical fixture rewritten to land under ``tenant_id``."""

    fx = default_fixture()
    if fx.header.tenant_id == tenant_id:
        return fx
    bare_token = tenant_id.removeprefix("tenant_") if tenant_id.startswith("tenant_") else tenant_id
    return fx.model_copy(
        update={
            "header": ConnectorHeader.model_construct(
                schema_name=fx.header.schema_name,
                generation=fx.header.generation,
                tenant_id=tenant_id,
                emitted_at=fx.header.emitted_at,
            ),
            "stores": [
                StoreRecord(**{**r.model_dump(), "tenant_id": bare_token})
                for r in fx.stores
            ],
            "people": [
                PersonRecord(**{**r.model_dump(), "tenant_id": bare_token})
                for r in fx.people
            ],
            "sales": [
                SalesRecord(**{**r.model_dump(), "tenant_id": bare_token})
                for r in fx.sales
            ],
            "targets": [
                TargetRecord(**{**r.model_dump(), "tenant_id": bare_token})
                for r in fx.targets
            ],
            "incentives": [
                IncentiveRecord(**{**r.model_dump(), "tenant_id": bare_token})
                for r in fx.incentives
            ],
        }
    )


__all__ = ["fixture_for_tenant"]
