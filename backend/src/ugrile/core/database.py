"""Database engine and session factory.

The session factory is intentionally tiny. ``get_engine`` is cached per process
to avoid leaking connections across hot-reload cycles; ``get_session`` returns
a context-managed :class:`sqlalchemy.orm.Session` for repository calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _build_engine() -> Engine:
    from sqlalchemy import create_engine

    settings = get_settings()
    url = settings.database_url
    connect_args: dict[str, Any] = {}
    if url.startswith("sqlite"):
        # SQLite enforces a single writer; allow shared cache so tests can
        # share an in-memory database across sessions if needed.
        connect_args = {"check_same_thread": False}
    engine = create_engine(
        url,
        echo=settings.database_echo,
        future=True,
        connect_args=connect_args,
    )
    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def reset_engine() -> None:
    """Drop the cached engine/session factory (used in tests)."""

    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_sessionmaker() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False, future=True
        )
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a session and commit/rollback based on caller behaviour.

    The repository layer is responsible for managing its own commits; this
    helper exists so the API layer can hold a session for an entire request.
    """

    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
