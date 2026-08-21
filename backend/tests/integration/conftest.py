"""Pytest fixtures for PostgreSQL integration tests.

The fixtures only run when ``UGRILE_PG_URL`` points at a reachable server;
otherwise the tests are skipped. ``UGRILE_PG_URL`` defaults to the local
ephemeral container used by ``make pg-up``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

import ugrile.repositories.models  # noqa: F401  (register models)
from ugrile.repositories.base import Base

DEFAULT_PG_URL = "postgresql+psycopg://grile:grile@127.0.0.1:55432/grile_test"


def _pg_url() -> str:
    return os.environ.get("UGRILE_PG_URL", DEFAULT_PG_URL)


def _assert_disposable_database(url: str) -> None:
    database = make_url(url).database
    if database in {None, "grile", "postgres", "template0", "template1"}:
        raise RuntimeError(
            "PostgreSQL integration tests require a dedicated disposable database; "
            "refusing to drop the application/admin database"
        )


def _ensure_default_test_database(url: str) -> None:
    """Create the dedicated local test DB without touching the app DB."""

    if os.environ.get("UGRILE_PG_URL") is not None:
        return
    parsed = make_url(url)
    database = parsed.database
    if database != "grile_test":  # defensive: the default must stay disposable
        raise RuntimeError("default PostgreSQL tests must use grile_test")
    admin = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database},
            ).scalar()
            if exists is None:
                conn.execute(text('CREATE DATABASE "grile_test"'))
    finally:
        admin.dispose()


@pytest.fixture()
def pg_engine() -> Iterator[Engine]:
    """Per-test engine: drops + recreates the schema for full isolation.

    Concurrent AC-02 races need a clean schema so the partial unique
    indexes never carry leftover rows from prior tests.
    """

    url = _pg_url()
    _assert_disposable_database(url)
    _ensure_default_test_database(url)
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - skip when DB unreachable
        pytest.skip(f"PostgreSQL integration tests skipped: {exc!r}")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def pg_session_factory(pg_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=pg_engine, autoflush=False, expire_on_commit=False, future=True)


@pytest.fixture()
def pg_session(pg_session_factory: sessionmaker[Session]) -> Iterator[Session]:
    s = pg_session_factory()
    try:
        yield s
    finally:
        s.close()
