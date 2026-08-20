"""API test fixtures — manager/admin user setup for the S3 endpoint tests.

The S3 grid / salary / close / reopen endpoints need both an admin and a
non-admin user in the database. The S2 conftest is per-test; we add the
manager user to every test that needs it via a local fixture.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ugrile.core import database
from ugrile.domain.enums import RoleName
from ugrile.repositories.models import User


@pytest.fixture()
def client(engine, faker_tenant):
    """Create a TestClient bound to the same in-memory DB as the fixtures."""

    with database.session_scope() as session:
        session.add(
            User(
                id="user_manager",
                tenant_id=faker_tenant["tenant_id"],
                email="manager@acme.example",
                display_name="Manager",
                role=RoleName.MANAGER.value,
            )
        )
        session.commit()
    from ugrile.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def client_no_manager(engine, faker_tenant):
    """TestClient without a manager user — for tests that exercise 401."""

    from ugrile.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
