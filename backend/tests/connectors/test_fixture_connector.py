"""Fixture connector tests (AC-03).

These tests prove:

* the fixture is structurally valid;
* applying it twice is idempotent;
* sales and target rows are stored with the canonical generation;
* the connector module does NOT import anything from Retail;
* two tenants running the same fixture internal codes never collide on
  the synthetic primary key (the AC-03 tenant-safety regression).
"""

from __future__ import annotations

import ast
import re
from decimal import Decimal
from pathlib import Path

from ugrile.connectors.fixtures import (
    FIXTURE_GENERATION,
    FIXTURE_TENANT_ID,
    FIXTURE_TENANT_TOKEN,
    default_fixture,
)
from ugrile.connectors.ingest import FixtureConnector
from ugrile.connectors.v1_types import (
    ConnectorHeader,
    PersonRecord,
    SalesRecord,
    StoreRecord,
    TargetRecord,
)
from ugrile.domain.identifiers import make_person_id, make_store_id, make_tenant_id
from ugrile.repositories.models import (
    Person,
    SalesStoreDay,
    Store,
    StoreTarget,
    Tenant,
)

RETAIL_PATH = Path("/opt/Mobiup/unihub-retail")
GRILE_SRC = Path(__file__).resolve().parents[2] / "src"
RETAIL_NAMES = {
    "unihub_retail",
    "unihub-retail",
    "mobiup_retail",
    "mobiup.retail",
    "mobiup.unihub_retail",
}


def _imports_in(path: Path) -> list[tuple[str, int, str]]:
    """Return the (module, lineno, line) of every Import/ImportFrom in the file.

    Docstring and comment mentions are excluded by parsing the AST instead of
    substring-matching the raw text.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append((module, node.lineno, f"from {module} import ..."))
    return imports


def test_default_fixture_is_well_formed():
    fx = default_fixture()
    assert fx.header.schema_name == "grile.connector.v1"
    assert fx.header.generation == FIXTURE_GENERATION
    assert fx.header.tenant_id == FIXTURE_TENANT_ID
    assert fx.stores
    assert fx.people
    assert fx.sales
    assert fx.targets
    for person in fx.people:
        assert any(s.internal_code == person.home_store_internal_code for s in fx.stores)
    for target in fx.targets:
        assert any(s.internal_code == target.store_internal_code for s in fx.stores)


def test_apply_fixture_is_idempotent(session):
    fx = default_fixture()
    connector = FixtureConnector(session)
    first = connector.apply(fx)
    session.commit()
    second = connector.apply(fx)
    session.commit()
    assert first["stores"] == second["stores"] == len(fx.stores)
    assert first["people"] == second["people"] == len(fx.people)
    assert first["sales"] == second["sales"] == len(fx.sales)
    assert first["targets"] == second["targets"] == len(fx.targets)
    sales = session.query(SalesStoreDay).count()
    assert sales == len(fx.sales)
    targets = session.query(StoreTarget).count()
    assert targets == len(fx.targets)


def test_apply_fixture_stores_sales_with_canonical_generation(session):
    fx = default_fixture()
    FixtureConnector(session).apply(fx)
    session.commit()
    rows = session.query(SalesStoreDay).all()
    assert all(r.generation == FIXTURE_GENERATION for r in rows)
    total = sum(float(r.amount) for r in rows)
    expected = sum(float(s.amount) for s in fx.sales)
    assert abs(total - expected) < 1e-6


def test_apply_fixture_stores_targets_with_canonical_generation(session):
    fx = default_fixture()
    FixtureConnector(session).apply(fx)
    session.commit()
    rows = session.query(StoreTarget).all()
    assert rows, "expected at least one target"
    for row in rows:
        assert row.kind == "MONTHLY_SALES"
        assert row.version == 1
        assert row.year == 2026
        assert row.month == 8


def test_two_tenants_isolation(session):
    """Two tenants must keep distinct Store/Person rows even when the
    internal codes match.

    This is the AC-03 tenant-safety regression. The connector must rewrite
    records per payload tenant and the synthetic ids must differ; otherwise
    a second tenant would silently inherit the first tenant's rows.
    """

    fx = default_fixture()
    FixtureConnector(session).apply(fx)
    session.commit()

    # Build a second fixture under a different tenant. The internal codes
    # intentionally overlap (bucuresti_center / alice) so the regression
    # catches a primary-key lookup that ignores tenant.
    other_token = "beta"
    other_tenant_id = make_tenant_id(other_token)
    header = ConnectorHeader(
        schema="grile.connector.v1",
        generation=FIXTURE_GENERATION,
        tenant_id=other_tenant_id,
        emitted_at="2026-08-20T12:00:00Z",
    )
    from ugrile.connectors.v1_types import ConnectorV1Payload as _V1

    other_payload = _V1(
        header=header,
        stores=[
            StoreRecord(
                tenant_id=other_tenant_id,
                internal_code="bucuresti_center",
                external_code="BUC-B",
                company_code="BETA",
                name="București Beta",
            )
        ],
        people=[
            PersonRecord(
                tenant_id=other_tenant_id,
                internal_code="alice",
                home_store_internal_code="bucuresti_center",
                display_name="Alice B",
            )
        ],
        sales=[
            SalesRecord(
                tenant_id=other_tenant_id,
                store_internal_code="bucuresti_center",
                business_date=fx.sales[0].business_date,
                amount=Decimal("1.00"),
            )
        ],
        targets=[
            TargetRecord(
                tenant_id=other_tenant_id,
                store_internal_code="bucuresti_center",
                year=2026,
                month=8,
                kind="MONTHLY_SALES",
                version=1,
                amount=Decimal("1000.00"),
            )
        ],
    )
    summary = FixtureConnector(session).apply(other_payload)
    session.commit()
    assert summary["stores"] == 1
    assert summary["people"] == 1
    assert summary["sales"] == 1
    assert summary["targets"] == 1

    # Each tenant now owns its own row. Same internal code, different
    # synthetic ids.
    expected_acme_store = make_store_id(FIXTURE_TENANT_TOKEN, "bucuresti_center")
    expected_beta_store = make_store_id(other_token, "bucuresti_center")
    assert expected_acme_store != expected_beta_store

    acme_stores = session.query(Store).filter_by(tenant_id=FIXTURE_TENANT_ID).all()
    beta_stores = session.query(Store).filter_by(tenant_id=other_tenant_id).all()
    assert len(acme_stores) == len(fx.stores)
    assert len(beta_stores) == 1
    assert acme_stores[0].id != beta_stores[0].id

    acme_people = session.query(Person).filter_by(tenant_id=FIXTURE_TENANT_ID).all()
    beta_people = session.query(Person).filter_by(tenant_id=other_tenant_id).all()
    assert len(beta_people) == 1
    assert acme_people[0].id != beta_people[0].id

    # The second tenant's store must NOT be reachable from the first
    # tenant's home_store_id: a person from acme cannot point at a beta
    # store (composite FK rejects it).
    cross_id = make_person_id(FIXTURE_TENANT_TOKEN, "carmen")
    cross_person = session.get(Person, cross_id)
    assert cross_person is not None
    assert cross_person.home_store_id != beta_stores[0].id


def test_grile_source_does_not_import_retail():
    """No Grile module may import from the Retail checkout."""

    offending: list[str] = []
    pattern = re.compile(r"retail|grile_salarii", re.IGNORECASE)
    for path in GRILE_SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for module, lineno, snippet in _imports_in(path):
            if pattern.search(module):
                offending.append(f"{path}:{lineno}: {snippet}")
    assert offending == [], "Grile source must not import from Retail/grile-salarii: " + "\n".join(
        offending
    )


def test_search_boundary_against_retail_working_tree():
    """Confirm no Grile source references the Retail checkout path in an import.

    The test only inspects filesystem paths. If ``/opt/Mobiup/unihub-retail``
    is missing (acceptable in disposable CI) the test still passes because
    the goal is to confirm Grile does not reach into it.
    """

    if not RETAIL_PATH.exists():
        return
    # The Retail path exists; ensure no Grile source imports from it.
    offending = []
    for path in GRILE_SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for module, lineno, snippet in _imports_in(path):
            if "unihub_retail" in module or "unihub-retail" in module:
                offending.append(f"{path}:{lineno}: {snippet}")
    assert offending == [], f"Retail path referenced in import: {offending}"


# Silence pytest's "imported but unused" warnings for the helper re-exports
# that the regression uses to assert on id shapes.
_ = (Tenant, StoreTarget)
