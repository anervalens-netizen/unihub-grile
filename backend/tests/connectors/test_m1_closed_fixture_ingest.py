from __future__ import annotations

import pytest

from ugrile.connectors.fixtures import FIXTURE_TENANT_ID, default_fixture
from ugrile.connectors.ingest import FixtureConnector
from ugrile.domain.enums import MonthState
from ugrile.domain.errors import ConflictError
from ugrile.repositories.models import Store
from ugrile.repositories.months import MonthRepository


def test_fixture_ingest_rejects_closed_period_before_any_payload_write(session):
    fixture = default_fixture()
    connector = FixtureConnector(session)
    connector.apply(fixture)
    session.commit()

    month = MonthRepository(session).get_or_create(FIXTURE_TENANT_ID, 2026, 8)
    month.state = MonthState.CLOSED.value
    month.revision = 7
    session.commit()
    month_id = month.id

    store = (
        session.query(Store)
        .filter_by(tenant_id=FIXTURE_TENANT_ID, internal_code="bucuresti_center")
        .one()
    )
    original_name = store.name
    modified = fixture.model_copy(
        update={
            "stores": [
                fixture.stores[0].model_copy(update={"name": "MUTATED AFTER CLOSE"}),
                *fixture.stores[1:],
            ]
        }
    )

    with pytest.raises(ConflictError) as exc_info:
        connector.apply(modified)

    assert exc_info.value.details["code"] == "MONTH_CLOSED"
    assert exc_info.value.details["closed_month_ids"] == [month_id]
    assert exc_info.value.details["closed_periods"] == ["2026-08"]

    session.rollback()
    session.expire_all()
    persisted = (
        session.query(Store)
        .filter_by(tenant_id=FIXTURE_TENANT_ID, internal_code="bucuresti_center")
        .one()
    )
    assert persisted.name == original_name
    locked_month = MonthRepository(session).get(month_id)
    assert locked_month.state == MonthState.CLOSED.value
    assert locked_month.revision == 7


def test_fixture_ingest_is_allowed_again_after_reopen(session):
    fixture = default_fixture()
    connector = FixtureConnector(session)
    connector.apply(fixture)
    session.commit()

    month = MonthRepository(session).get_or_create(FIXTURE_TENANT_ID, 2026, 8)
    month.state = MonthState.REOPENED.value
    month.revision = 8
    session.commit()

    modified = fixture.model_copy(
        update={
            "stores": [
                fixture.stores[0].model_copy(update={"name": "Corrected after reopen"}),
                *fixture.stores[1:],
            ]
        }
    )
    result = connector.apply(modified)
    session.commit()

    assert result["sales"] == len(fixture.sales)
    persisted = (
        session.query(Store)
        .filter_by(tenant_id=FIXTURE_TENANT_ID, internal_code="bucuresti_center")
        .one()
    )
    assert persisted.name == "Corrected after reopen"
