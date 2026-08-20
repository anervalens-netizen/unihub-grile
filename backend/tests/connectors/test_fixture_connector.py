"""Fixture connector tests (AC-03).

These tests prove:

* the fixture is structurally valid;
* applying it twice is idempotent;
* sales rows are stored with the canonical generation;
* the connector module does NOT import anything from Retail.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from ugrile.connectors.fixtures import (
    FIXTURE_GENERATION,
    FIXTURE_TENANT_ID,
    default_fixture,
)
from ugrile.connectors.ingest import FixtureConnector
from ugrile.repositories.models import SalesStoreDay

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
    for person in fx.people:
        assert any(s.internal_code == person.home_store_internal_code for s in fx.stores)


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
    sales = session.query(SalesStoreDay).count()
    assert sales == len(fx.sales)


def test_apply_fixture_stores_sales_with_canonical_generation(session):
    fx = default_fixture()
    FixtureConnector(session).apply(fx)
    session.commit()
    rows = session.query(SalesStoreDay).all()
    assert all(r.generation == FIXTURE_GENERATION for r in rows)
    total = sum(float(r.amount) for r in rows)
    expected = sum(float(s.amount) for s in fx.sales)
    assert abs(total - expected) < 1e-6


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
