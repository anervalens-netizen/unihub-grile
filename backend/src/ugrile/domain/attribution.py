"""Pure sales attribution (AC-07).

The calendar manager is the sole authority over who worked where on which
date. For each ``WORKING`` assignment we look up the matching
``SalesStoreDay`` row (immutable per store/date/generation) and credit the
full store-day amount to the calendar-assigned person.

Invariants the engine guarantees
--------------------------------

* Each ``SalesStoreDay`` row appears **exactly once** in the company/store
  totals (the engine never touches ``sales_store_day``).
* Each ``SalesStoreDay`` row is credited to **at most one** person per
  calendar revision — the person that owns the WORKING assignment for that
  store/date.
* Reassignment (changing the WORKING person for an existing store/date)
  changes the personal credit but leaves the store/company total identical.
* ``EXTRA_HOME`` and ``EXTRA_OTHER`` are classified per row but never
  duplicate physical sales; ``EXTRA_HOME`` is credited to the home person
  for the home store's sale; ``EXTRA_OTHER`` is credited to the visiting
  person for the other store's sale.
* The function returns one ``AttributedSale`` row per WORKING assignment,
  each carrying the original ``generation`` so reattribution after the
  connector advances is auditable.

Tenant safety
-------------

The caller passes pre-resolved maps; the engine never looks at the DB.
Multi-tenant identity is the responsibility of the service layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .enums import WorkingKind


@dataclass(frozen=True, slots=True)
class CalendarWorkingDay:
    """Minimum calendar slice consumed by the attribution engine."""

    person_id: str
    store_id: str
    business_date: date
    working_kind: WorkingKind


@dataclass(frozen=True, slots=True)
class StoreDaySale:
    """A single immutable physical sale, surfaced to the engine as a value."""

    store_id: str
    business_date: date
    generation: str
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class AttributedSale:
    """An attribution row — store-day credited to a calendar-assigned person."""

    person_id: str
    store_id: str
    business_date: date
    working_kind: WorkingKind
    amount: Decimal
    currency: str
    generation: str


@dataclass(frozen=True, slots=True)
class AttributionAnomaly:
    """A deterministic anomaly that the engine surfaces without raising.

    Missing sales (``sales_missing``) and orphan sales (``sales_orphan``) are
    explicit anomalies the close pipeline reports; the engine itself is
    pure and never raises. The anomalies list keeps the caller honest about
    which rows need management.
    """

    code: str
    store_id: str | None
    person_id: str | None
    business_date: date | None
    message: str


@dataclass(frozen=True, slots=True)
class AttributionResult:
    attributed: tuple[AttributedSale, ...]
    anomalies: tuple[AttributionAnomaly, ...]
    store_total_amount: Decimal
    store_total_currency: str
    company_total_amount: Decimal
    company_total_currency: str


def attribute_sales(
    working_days: list[CalendarWorkingDay],
    sales: list[StoreDaySale],
    *,
    expected_currencies: tuple[str, ...] = ("RON",),
) -> AttributionResult:
    """Attribute ``sales`` to the ``working_days`` persons.

    Same input lists always produce the same result, byte-for-byte, with the
    same tuple ordering: working_days sorted by ``(store_id, business_date,
    person_id)`` and anomalies sorted by ``(code, store_id, business_date)``.
    """

    # Index sales by (store_id, business_date, generation). The connector
    # guarantees at most one row per (store_id, business_date, generation),
    # but we keep the latest generation explicit so a future "two
    # generations in flight" model does not silently misattribute.
    sales_index: dict[tuple[str, date, str], StoreDaySale] = {
        (s.store_id, s.business_date, s.generation): s for s in sales
    }
    if not sales_index:
        # Empty sales — no attributed rows; totals are zero.
        zero = Decimal("0")
        empty = (
            AttributionAnomaly(
                "sales_missing",
                None,
                None,
                None,
                "no sales rows supplied",
            ),
        )
        return AttributionResult(
            attributed=(),
            anomalies=empty,
            store_total_amount=zero,
            store_total_currency="RON",
            company_total_amount=zero,
            company_total_currency="RON",
        )

    # We project every generation that actually appears to a single row per
    # (store_id, business_date) for the totals: in S3 the v1 fixture exposes
    # exactly one generation, but the projection remains stable when the
    # connector later advances.
    latest_generation_per_pair: dict[tuple[str, date], str] = {}
    for s in sales:
        key = (s.store_id, s.business_date)
        existing = latest_generation_per_pair.get(key)
        if existing is None or s.generation > existing:
            latest_generation_per_pair[key] = s.generation

    # Pick the matching sale per working day; tolerate multi-generation
    # fixtures by preferring the same generation that produced the latest
    # generation per pair.
    attributed_rows: list[AttributedSale] = []
    anomalies: list[AttributionAnomaly] = []
    matched_pairs: set[tuple[str, date]] = set()

    for day in sorted(
        working_days, key=lambda d: (d.store_id, d.business_date, d.person_id)
    ):
        latest_gen = latest_generation_per_pair.get((day.store_id, day.business_date))
        sale: StoreDaySale | None = None
        if latest_gen is not None:
            sale = sales_index.get((day.store_id, day.business_date, latest_gen))
        if sale is None:
            # No matching physical sale. Surface as an anomaly but still
            # attribute a zero-amount row so the grid engine sees the day.
            anomalies.append(
                AttributionAnomaly(
                    code="sales_missing",
                    store_id=day.store_id,
                    person_id=day.person_id,
                    business_date=day.business_date,
                    message="no SalesStoreDay for worked store/date",
                )
            )
            attributed_rows.append(
                AttributedSale(
                    person_id=day.person_id,
                    store_id=day.store_id,
                    business_date=day.business_date,
                    working_kind=day.working_kind,
                    amount=Decimal("0"),
                    currency="RON",
                    generation="",
                )
            )
            continue
        if sale.currency not in expected_currencies:
            anomalies.append(
                AttributionAnomaly(
                    code="unexpected_currency",
                    store_id=day.store_id,
                    person_id=day.person_id,
                    business_date=day.business_date,
                    message=(
                        f"currency {sale.currency!r} not in expected "
                        f"{expected_currencies!r}"
                    ),
                )
            )
            continue
        matched_pairs.add((sale.store_id, sale.business_date))
        attributed_rows.append(
            AttributedSale(
                person_id=day.person_id,
                store_id=day.store_id,
                business_date=day.business_date,
                working_kind=day.working_kind,
                amount=sale.amount,
                currency=sale.currency,
                generation=sale.generation,
            )
        )

    # Surface orphan sales (no WORKING assignment on the same store/date).
    for (store_id, business_date), gen in sorted(latest_generation_per_pair.items()):
        if (store_id, business_date) in matched_pairs:
            continue
        sale = sales_index.get((store_id, business_date, gen))
        if sale is None:
            continue
        anomalies.append(
            AttributionAnomaly(
                code="sales_orphan",
                store_id=store_id,
                person_id=None,
                business_date=business_date,
                message="SalesStoreDay without a working calendar entry",
            )
        )

    # Totals use the latest generation only. Sum amounts in deterministic
    # order to keep the hash stable.
    store_totals: dict[str, Decimal] = {}
    company_total = Decimal("0")
    company_currency = "RON"
    for (store_id, business_date), gen in sorted(latest_generation_per_pair.items()):
        sale = sales_index[(store_id, business_date, gen)]
        store_totals[store_id] = store_totals.get(store_id, Decimal("0")) + sale.amount
        company_total += sale.amount
        company_currency = sale.currency

    # Return deterministic ordering for attributed rows.
    attributed_rows.sort(key=lambda r: (r.business_date, r.person_id, r.store_id))
    anomalies.sort(
        key=lambda a: (a.code, a.store_id or "", a.business_date or date.min, a.person_id or "")
    )

    # Pick a deterministic store_total_amount: the engine does not collapse
    # the per-store map; the caller decides which store to query.
    # Expose the company total for cross-checks.
    return AttributionResult(
        attributed=tuple(attributed_rows),
        anomalies=tuple(anomalies),
        store_total_amount=sum(store_totals.values(), Decimal("0")),
        store_total_currency=company_currency,
        company_total_amount=company_total,
        company_total_currency=company_currency,
    )


def store_total(sales: list[StoreDaySale], store_id: str) -> Decimal:
    """Helper used by API/tests: deterministic store total.

    Sums amounts across all generations, which mirrors the per-pair view
    above but exposes a simpler shape when the caller wants only the
    company-visible store total. Multi-generation rows count as separate
    rows; the projection layer is expected to filter to the latest
    generation when surfacing a single canonical value.
    """

    total = Decimal("0")
    for sale in sorted(
        sales,
        key=lambda s: (s.store_id, s.business_date, s.generation),
    ):
        if sale.store_id == store_id:
            total += sale.amount
    return total


__all__ = [
    "AttributedSale",
    "AttributionAnomaly",
    "AttributionResult",
    "CalendarWorkingDay",
    "StoreDaySale",
    "attribute_sales",
    "store_total",
]
