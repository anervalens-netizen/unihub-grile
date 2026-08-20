"""Integration tests that require a real PostgreSQL server.

Each module in this directory is marked with ``pytest.mark.postgres`` and
skipped when the ``UGRILE_PG_URL`` environment variable is unset. The
``conftest.py`` fixture is responsible for wiring the engine.
"""
