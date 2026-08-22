from __future__ import annotations

import contextlib
import threading

import pytest

from ugrile.domain.enums import CloseAction, MonthState, RoleName
from ugrile.domain.errors import MonthStateError
from ugrile.repositories.close import MonthCloseEventRepository
from ugrile.repositories.models import Tenant
from ugrile.repositories.months import MonthRepository
from ugrile.services.close import CloseOutcome, CloseService, ReopenRequest

pytestmark = pytest.mark.postgres


def _attempt_reopen(
    session_factory,
    *,
    tenant_id: str,
    month_id: str,
    barrier: threading.Barrier,
    outcome: dict[str, BaseException | CloseOutcome | None],
    name: str,
) -> None:
    session = session_factory()
    try:
        barrier.wait()
        result = CloseService(session).reopen_month(
            tenant_id=tenant_id,
            month_id=month_id,
            request=ReopenRequest(
                actor_id=f"admin_{name}",
                role_value=RoleName.ADMIN.value,
                reason=f"concurrent correction {name}",
            ),
        )
        session.commit()
        outcome[name] = result
    except BaseException as exc:
        outcome[name] = exc
        with contextlib.suppress(Exception):  # pragma: no cover
            session.rollback()
    finally:
        session.close()


def test_two_concurrent_reopen_attempts_serialize_on_postgres(
    pg_session,
    pg_session_factory,
):
    tenant_id = "tenant_reopen_race"
    pg_session.add(
        Tenant(
            id=tenant_id,
            name="Reopen Race",
            timezone="Europe/Bucharest",
        )
    )
    pg_session.commit()

    month = MonthRepository(pg_session).get_or_create(tenant_id, 2026, 8)
    month.state = MonthState.CLOSED.value
    month.revision = 4
    pg_session.flush()
    MonthCloseEventRepository(pg_session).append(
        tenant_id=tenant_id,
        month_id=month.id,
        action=CloseAction.CLOSE,
        previous_state=MonthState.OPEN.value,
        new_state=MonthState.CLOSED.value,
        revision_before=3,
        revision_after=4,
        actor_id="admin_seed",
        reason=None,
        blockers=[],
    )
    pg_session.commit()
    month_id = month.id

    barrier = threading.Barrier(2)
    outcome: dict[str, BaseException | CloseOutcome | None] = {}
    threads = [
        threading.Thread(
            target=_attempt_reopen,
            kwargs={
                "session_factory": pg_session_factory,
                "tenant_id": tenant_id,
                "month_id": month_id,
                "barrier": barrier,
                "outcome": outcome,
                "name": name,
            },
        )
        for name in ("A", "B")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [name for name, value in outcome.items() if isinstance(value, CloseOutcome)]
    losers = [name for name, value in outcome.items() if not isinstance(value, CloseOutcome)]
    assert len(winners) == 1, f"expected one reopen winner, got {outcome}"
    assert len(losers) == 1, f"expected one reopen loser, got {outcome}"
    assert isinstance(outcome[losers[0]], MonthStateError)

    pg_session.expire_all()
    fresh = MonthRepository(pg_session).get(month_id)
    assert fresh.state == MonthState.REOPENED.value
    assert fresh.revision == 5

    repo = MonthCloseEventRepository(pg_session)
    events = repo.list_for_month(tenant_id=tenant_id, month_id=month_id)
    assert [event.action for event in events] == ["CLOSE", "REOPEN"]
    assert repo.verify_chain(tenant_id=tenant_id, month_id=month_id) == []
    assert events[1].previous_event_digest == events[0].event_digest
