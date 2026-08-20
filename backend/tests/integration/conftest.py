"""Pytest fixtures for PostgreSQL integration tests.

The fixtures only run when ``UGRILE_PG_URL`` points at a reachable server;
otherwise the tests are skipped. ``UGRILE_PG_URL`` defaults to the local
ephemeral container used by ``make pg-up``.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import ugrile.repositories.models  # noqa: F401  (register models)
from ugrile.repositories.base import Base

DEFAULT_PG_URL = "postgresql+psycopg://grile:grile@127.0.0.1:55432/grile"


def _pg_url() -> str:
    return os.environ.get("UGRILE_PG_URL", DEFAULT_PG_URL)


@pytest.fixture()
def pg_engine():
    """Per-test engine: drops + recreates the schema for full isolation.

    Concurrent AC-02 races need a clean schema so the partial unique
    indexes never carry leftover rows from prior tests.
    """

    url = _pg_url()
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
def pg_session_factory(pg_engine):
    return sessionmaker(bind=pg_engine, autoflush=False, expire_on_commit=False, future=True)


@pytest.fixture()
def pg_session(pg_session_factory) -> Session:
    s = pg_session_factory()
    try:
        yield s
    finally:
        s.close()
