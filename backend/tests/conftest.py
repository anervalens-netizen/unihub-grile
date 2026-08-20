"""Shared pytest fixtures.

The S1 test stack uses SQLite in-memory for unit/domain/repository tests
because the foundation only needs to prove the seam and the AC-02 partial
unique indexes — both translate cleanly to SQLite (see
``tests/repositories/test_db_constraints.py`` for the cross-dialect proof).

Tests that explicitly need PostgreSQL semantics are gated behind the
``postgres`` marker and are exercised by the dedicated CI job described in
``docs/operations/local-commands.md``.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import ugrile.repositories.models  # noqa: F401  (register models)
from ugrile.core.database import reset_engine
from ugrile.repositories.base import Base

# Force the test env BEFORE importing anything that caches settings.
os.environ.setdefault("APP_ENV", "test")
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"


@pytest.fixture(autouse=True)
def _reset_db_state():
    """Reset the cached engine between tests so the test fixture owns the DB."""

    reset_engine()
    yield
    reset_engine()


@pytest.fixture()
def engine():
    """SQLite in-memory engine with the foundation schema.

    Uses a StaticPool so the in-memory database is shared across all
    connections within the test session. The engine is also installed in
    :mod:`ugrile.core.database` so application code (worker, repositories,
    API) talks to the same database.
    """

    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)

    from ugrile.core import database

    SessionLocal = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    database._engine = eng  # type: ignore[attr-defined]
    database._SessionLocal = SessionLocal  # type: ignore[attr-defined]

    try:
        yield eng
    finally:
        database._engine = None  # type: ignore[attr-defined]
        database._SessionLocal = None  # type: ignore[attr-defined]
        eng.dispose()


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@pytest.fixture()
def session(session_factory):
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def faker_tenant(session):
    from ugrile.domain.enums import RoleName
    from ugrile.domain.identifiers import (
        make_person_id,
        make_store_id,
        make_tenant_id,
    )
    from ugrile.repositories.models import Person, Store, Tenant, User

    tenant_token = "acme"
    tenant_id = make_tenant_id(tenant_token)
    tenant = Tenant(id=tenant_id, name="Acme", timezone="Europe/Bucharest")
    store = Store(
        id=make_store_id(tenant_token, "s1"),
        tenant_id=tenant_id,
        company_code="MOBIUP",
        internal_code="s1",
        name="S1",
    )
    other_store = Store(
        id=make_store_id(tenant_token, "s2"),
        tenant_id=tenant_id,
        company_code="MOBIUP",
        internal_code="s2",
        name="S2",
    )
    person_a = Person(
        id=make_person_id(tenant_token, "a"),
        tenant_id=tenant_id,
        internal_code="a",
        display_name="Alice",
        home_store_id=store.id,
    )
    person_b = Person(
        id=make_person_id(tenant_token, "b"),
        tenant_id=tenant_id,
        internal_code="b",
        display_name="Bob",
        home_store_id=store.id,
    )
    person_c = Person(
        id=make_person_id(tenant_token, "c"),
        tenant_id=tenant_id,
        internal_code="c",
        display_name="Carmen",
        home_store_id=other_store.id,
    )
    user = User(
        id="user_admin",
        tenant_id=tenant_id,
        email="admin@acme.example",
        display_name="Admin",
        role=RoleName.ADMIN.value,
    )
    session.add_all([tenant, store, other_store, person_a, person_b, person_c, user])
    session.commit()
    return {
        "tenant_id": tenant_id,
        "tenant_token": tenant_token,
        "store_id": store.id,
        "other_store_id": other_store.id,
        "person_a_id": person_a.id,
        "person_b_id": person_b.id,
        "person_c_id": person_c.id,
        "user_id": user.id,
    }


@pytest.fixture()
def faker_second_tenant(session):
    """A second tenant used by multi-tenant isolation tests.

    Shares no row with ``faker_tenant``. The two stores have the same
    internal code (``s1``) but the composite FK plus the tenant-scoped
    ids prove they cannot collide.
    """

    from ugrile.domain.enums import RoleName
    from ugrile.domain.identifiers import (
        make_person_id,
        make_store_id,
        make_tenant_id,
    )
    from ugrile.repositories.models import Person, Store, Tenant, User

    tenant_token = "beta"
    tenant_id = make_tenant_id(tenant_token)
    tenant = Tenant(id=tenant_id, name="Beta", timezone="Europe/Bucharest")
    store = Store(
        id=make_store_id(tenant_token, "s1"),
        tenant_id=tenant_id,
        company_code="BETA",
        internal_code="s1",
        name="Beta S1",
    )
    person = Person(
        id=make_person_id(tenant_token, "alice"),
        tenant_id=tenant_id,
        internal_code="alice",
        display_name="Alice B",
        home_store_id=store.id,
    )
    user = User(
        id="user_admin_beta",
        tenant_id=tenant_id,
        email="admin@beta.example",
        display_name="Beta Admin",
        role=RoleName.ADMIN.value,
    )
    session.add_all([tenant, store, person, user])
    session.commit()
    return {
        "tenant_id": tenant_id,
        "tenant_token": tenant_token,
        "store_id": store.id,
        "person_id": person.id,
        "user_id": user.id,
    }
