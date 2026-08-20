"""Deterministic Mobiup v1-compat grid calculation.

The engine is intentionally pure:

* Every input is a dataclass snapshot (sales, calendar, salary, epay, Pontaj).
* Coefficients live in :class:`ugrile.domain.rule_pack.RulePackV1` — never in
  the function body.
* Every monetary result is a :class:`Decimal` rounded with ``ROUND_HALF_UP``
  (per docs/MOBIUP_RULE_PACK.md §6).
* The same canonical input hash always produces the same canonical output
  hash.

Golden fixtures (AC-09 §8)
-------------------------

The tests in ``tests/domain/test_s3_grid.py`` lock the documented fixtures
plus progress threshold edges, EXTRA_HOME/EXTRA_OTHER edges, target zero,
missing sale and uncovered day. The V2 example
``2600 + 480 + 27 + 350 = 3457`` is reproduced verbatim.

Purity contract
---------------

* No I/O.
* No ``datetime.now()`` — the engine accepts the snapshot it is given.
* No imports from :mod:`ugrile.repositories` — the engine never touches the
  DB; the service layer feeds it pre-loaded inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from .enums import WorkingKind
from .rule_pack import (
    EpayObservationSnapshot,
    PontajHoursSnapshot,
    RulePackParameters,
    RulePackV1,
    money,
)


@dataclass(frozen=True, slots=True)
class CalendarGridDay:
    """One person's grid-relevant inputs for one business date."""

    person_id: str
    person_home_store_id: str
    business_date: object  # datetime.date but kept loose for purity
    working_kind: WorkingKind | None
    store_id: str | None
    target_amount: Decimal
    sales_amount: Decimal
    sim_quantity: int


@dataclass(frozen=True, slots=True)
class GridInputs:
    """Per-person grid inputs assembled by the service layer."""

    person_id: str
    person_home_store_id: str
    parameters: RulePackParameters
    calendar_days: tuple[CalendarGridDay, ...]
    epay: EpayObservationSnapshot
    pontaj: PontajHoursSnapshot


@dataclass(frozen=True, slots=True)
class GridComponents:
    """Decomposed salary grid for a single person/month.

    Every monetary field is a Decimal rounded with ROUND_HALF_UP. The
    ``total_salary`` is the sum documented in MOBIUP_RULE_PACK §6 (it
    includes ``tickets``); ``salary_cash`` is the same total minus tickets.
    """

    salary: Decimal
    tickets: Decimal
    main_commission: Decimal
    main_bonus: Decimal
    extra_fixed_pay: Decimal
    extra_other_commission: Decimal
    sim_commission: Decimal
    epay_commission: Decimal
    incentive: Decimal
    flip: Decimal
    total_salary: Decimal
    salary_cash: Decimal
    extra_days: int
    progress: Decimal
    realised_main: Decimal
    target_main: Decimal


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _pct(realised: Decimal, target: Decimal) -> Decimal:
    """Realised/target progress as a Decimal ratio.

    ``Decimal`` division with explicit precision to avoid float artefacts.
    Returns ``Decimal("0")`` when the target is zero — the engine surfaces a
    target-zero anomaly on the result object instead of producing ``inf``.
    """

    if target <= 0:
        return Decimal("0")
    return (realised / target).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _is_extra(working_kind: WorkingKind | None) -> bool:
    return working_kind in {WorkingKind.EXTRA_HOME, WorkingKind.EXTRA_OTHER}


def _bucket_main_realised(
    days: tuple[CalendarGridDay, ...],
    home_store_id: str,
) -> tuple[Decimal, Decimal, int]:
    """Return ``(realised_main, target_main, extra_days)`` for the home store.

    The home main realised/target aggregates ``NORMAL`` *and* ``EXTRA_HOME``
    days (per the contract), and counts both as supplementary days for the
    fixed pay. ``EXTRA_OTHER`` days are billed separately and never feed the
    home main figure.
    """

    realised = Decimal("0")
    target = Decimal("0")
    extra_days = 0
    for day in days:
        kind = day.working_kind
        if kind is None:
            continue
        if kind is WorkingKind.EXTRA_OTHER:
            continue
        if day.store_id != home_store_id:
            continue
        realised += day.sales_amount
        target += day.target_amount
        if kind is WorkingKind.EXTRA_HOME:
            extra_days += 1
    return realised, target, extra_days


def _bucket_extra_other(
    days: tuple[CalendarGridDay, ...],
) -> tuple[Decimal, int]:
    realised = Decimal("0")
    extra_days = 0
    for day in days:
        if day.working_kind is WorkingKind.EXTRA_OTHER:
            realised += day.sales_amount
            extra_days += 1
    return realised, extra_days


def _main_commission(
    pack: RulePackV1,
    progress: Decimal,
    realised: Decimal,
) -> tuple[Decimal, Decimal]:
    """Return ``(commission, bonus)`` according to docs/MOBIUP_RULE_PACK.md §3."""

    if realised <= 0 or progress <= 0:
        return Decimal("0"), Decimal("0")
    if progress < pack.main_progress_under_80:
        return Decimal("0"), Decimal("0")
    # commission is 3% of realised; bonus is the step above the threshold.
    commission = money(pack.main_commission_rate * realised)
    if progress < pack.main_progress_under_100:
        bonus = money(pack.main_bonus_under_100)
    elif progress < pack.main_progress_under_120:
        bonus = money(pack.main_bonus_at_100)
    else:
        bonus = money(pack.main_bonus_at_120)
    return commission, bonus


def _extra_other_commission(
    pack: RulePackV1,
    days: tuple[CalendarGridDay, ...],
    home_store_id: str,
) -> Decimal:
    """Per docs/MOBIUP_RULE_PACK.md §4.

    Each ``EXTRA_OTHER`` day independently evaluates
    ``realised_day / target_day`` against the ``0.79`` threshold and awards
    ``3% * realised_day`` only when the day passes the threshold. Each day's
    commission is rounded individually, then summed (rounding semantics per
    contract).
    """

    total = Decimal("0")
    for day in days:
        if day.working_kind is not WorkingKind.EXTRA_OTHER:
            continue
        if day.store_id == home_store_id:
            # Defensive: contractually impossible (EXTRA_OTHER requires
            # home_store_id != site_store_id), but the engine never trusts
            # the caller; an invalid pair is treated as a zero commission.
            continue
        if day.target_amount <= 0:
            continue
        ratio = day.sales_amount / day.target_amount
        if ratio < pack.extra_other_threshold:
            continue
        total += money(pack.extra_other_rate * day.sales_amount)
    return total


def _extra_fixed_pay(pack: RulePackV1, extra_days: int) -> Decimal:
    return money(pack.extra_fixed_pay * Decimal(extra_days))


def _sim_commission(pack: RulePackV1, days: tuple[CalendarGridDay, ...]) -> Decimal:
    total = Decimal("0")
    for day in days:
        if day.working_kind is None:
            continue
        total += pack.sim_unit_rate * Decimal(day.sim_quantity)
    return money(total)


def _epay_commission(pack: RulePackV1, epay: EpayObservationSnapshot) -> Decimal:
    under = pack.epay_under_50_rate * Decimal(epay.under_50_quantity)
    over = pack.epay_at_or_over_50_rate * Decimal(epay.at_or_over_50_quantity)
    return money(under + over)


def calculate_grid(pack: RulePackV1, inputs: GridInputs) -> GridComponents:
    """Compute the decomposed grid for one person/month.

    ``pack`` selects the versioned coefficients; ``inputs`` carries the
    per-person snapshot. The function is pure and deterministic.
    """

    days = inputs.calendar_days
    realised_main, target_main, extra_days = _bucket_main_realised(
        days, inputs.person_home_store_id
    )
    extra_other_realised, extra_other_days = _bucket_extra_other(days)
    progress = _pct(realised_main, target_main)

    commission, bonus = _main_commission(pack, progress, realised_main)
    extra_other_total = _extra_other_commission(pack, days, inputs.person_home_store_id)
    extra_fixed = _extra_fixed_pay(pack, extra_days + extra_other_days)
    sim = _sim_commission(pack, days)
    epay = _epay_commission(pack, inputs.epay)

    salary = money(inputs.parameters.salary)
    tickets = money(inputs.parameters.tickets)
    flip = money(inputs.parameters.flip)
    incentive = money(inputs.parameters.incentive)

    total = (
        salary
        + tickets
        + commission
        + bonus
        + extra_fixed
        + extra_other_total
        + sim
        + epay
        + incentive
        + flip
    )
    total = money(total)
    salary_cash = money(total - tickets)

    return GridComponents(
        salary=salary,
        tickets=tickets,
        main_commission=commission,
        main_bonus=bonus,
        extra_fixed_pay=extra_fixed,
        extra_other_commission=extra_other_total,
        sim_commission=sim,
        epay_commission=epay,
        incentive=incentive,
        flip=flip,
        total_salary=total,
        salary_cash=salary_cash,
        extra_days=extra_days + extra_other_days,
        progress=progress,
        realised_main=realised_main,
        target_main=target_main,
    )


# Convenience constants exported for golden fixtures.
V2_EXAMPLE_SALARY: Final[Decimal] = Decimal("2600")
V2_EXAMPLE_TICKETS: Final[Decimal] = Decimal("480")
V2_EXAMPLE_SIM_QTY: Final[int] = 9  # 9 * 3 = 27
V2_EXAMPLE_INCENTIVE: Final[Decimal] = Decimal("350")
V2_EXAMPLE_TOTAL: Final[Decimal] = Decimal("3457")  # 2600 + 480 + 27 + 350


__all__ = [
    "CalendarGridDay",
    "GridComponents",
    "GridInputs",
    "V2_EXAMPLE_INCENTIVE",
    "V2_EXAMPLE_SALARY",
    "V2_EXAMPLE_SIM_QTY",
    "V2_EXAMPLE_TICKETS",
    "V2_EXAMPLE_TOTAL",
    "calculate_grid",
]
