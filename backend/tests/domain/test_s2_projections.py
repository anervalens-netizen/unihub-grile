from datetime import date, time
from decimal import Decimal

from ugrile.domain.calendar import SiteDayAssignment
from ugrile.domain.enums import DayStatus, WorkingKind
from ugrile.domain.projections import (
    HoursConfig,
    derive_person_calendar,
    derive_pontaj,
    derive_store_coverage,
)


def test_calendar_authority_derives_person_store_and_pontaj():
    assignments = [
        SiteDayAssignment(
            "store_a", "person_a", date(2026, 8, 1), DayStatus.WORKING, WorkingKind.NORMAL
        )
    ]
    people = derive_person_calendar(assignments, ["person_a", "person_b"], [date(2026, 8, 1)])
    assert [(x.person_id, x.status) for x in people] == [
        ("person_a", DayStatus.WORKING),
        ("person_b", DayStatus.OFF),
    ]
    coverage = derive_store_coverage(assignments, ["store_a", "store_b"], [date(2026, 8, 1)])
    assert [x.covered for x in coverage] == [True, False]
    pontaj = derive_pontaj(people, HoursConfig(time(10), time(22), 60))
    assert pontaj[0].hours == Decimal("11")
    assert pontaj[1].hours == Decimal("0")
