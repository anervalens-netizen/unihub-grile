"""Versioned Mobiup rule pack — typed parameters and hash function.

The rule pack is the *only* place where Mobiup-specific business coefficients
live. The generic engine in :mod:`ugrile.domain.grid` selects a rule pack by
version and never inlines a coefficient. This keeps the engine portable: a
different tenant would swap the rule pack without touching the calculation
itself.

Confirmed business decisions (S3)
---------------------------------

* Salariul fix si tichetele vin dintr-un master HR/payroll effective-dated
  (``salary_master``); managerul de program nu le editeaza.
* Ajustarea legacy ``Flip`` ramane activa si versionata.
* Sarbatorile folosesc un calendar legal Romania versionat cu override admin;
  in S3 ele sunt doar un marker informativ, fara efect automat asupra
  programului, Pontajului, targetului sau platii.
* Close este admin-only; reopen este admin-only si auditat.

Grid-compatible primitives
--------------------------

``RulePackV1`` carries every numeric coefficient the V1 grile-Salarii/V2
sheets contract documents.  Each value is a :class:`Decimal` with explicit
rounding (``ROUND_HALF_UP``) so the same canonical input produces the same
canonical output.

* ``main_commission_rate`` -- 3% aplicat la ``realizat_principal``.
* ``main_bonus_100_threshold`` / ``main_bonus_120_threshold`` -- pragurile
  progresului (1.00 / 1.20).
* ``main_bonus_under_100`` -- 0 RON (sub 80%) sau 0 RON (intre 80-100% fara
  bonus). Documentat ca zero pentru sub-prag.
* ``main_bonus_at_100`` -- 200 RON pentru 100-120%.
* ``main_bonus_at_120`` -- 400 RON pentru >=120%.
* ``extra_fixed_pay`` -- 150 RON pe zi ``EXTRA_HOME`` / ``EXTRA_OTHER``.
* ``extra_other_threshold`` -- 0.79 (pragul tehnic exact V1).
* ``extra_other_rate`` -- 3% pentru ``EXTRA_OTHER`` peste prag.
* ``sim_unit_rate`` -- 3 RON per cantitate.
* ``epay_under_50_rate`` -- 5 RON per cantitate.
* ``epay_at_or_over_50_rate`` -- 12 RON per cantitate.
* ``flip_default`` -- 0 (ajustarea este explicita prin snapshot, nu o
  constanta invizibila).
* ``salary_master_default`` -- 0 RON fix / 0 RON tichete; salariul si
  tichetele trebuie furnizate din master, altfel contributia este zero si se
  ridica un marker explicit.

Hashing
-------

``rule_pack_canonical_bytes`` deterministically serialises the rule pack to
JSON so two deployments of the same version always hash identically. The
inputs hash (``inputs_hash``) is built from the same primitive plus the
caller inputs (calendar revision, sales generation, salary snapshot hash).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

# Rule pack version. Bumping this requires an explicit contract decision and
# must not be done silently. The current value matches docs/MOBIUP_RULE_PACK.md
# ("mobiup-v1-compat").
RULE_PACK_VERSION: Final[str] = "mobiup-v1-compat"


def _d(value: str) -> Decimal:
    """Typed Decimal coercion; never let a float reach the engine."""

    return Decimal(value)


@dataclass(frozen=True, slots=True)
class RulePackV1:
    """Mobiup v1-compat rule pack. Immutable and JSON-serialisable."""

    version: str
    main_commission_rate: Decimal
    main_bonus_under_100: Decimal
    main_bonus_at_100: Decimal
    main_bonus_at_120: Decimal
    main_progress_under_80: Decimal
    main_progress_under_100: Decimal
    main_progress_under_120: Decimal
    extra_fixed_pay: Decimal
    extra_other_threshold: Decimal
    extra_other_rate: Decimal
    sim_unit_rate: Decimal
    epay_under_50_rate: Decimal
    epay_at_or_over_50_rate: Decimal
    flip_default: Decimal
    salary_default: Decimal
    ticket_default: Decimal
    incentive_default: Decimal
    rounding: str

    @classmethod
    def default(cls) -> RulePackV1:
        """Return the canonical Mobiup v1-compat coefficients."""

        return cls(
            version=RULE_PACK_VERSION,
            main_commission_rate=_d("0.03"),
            main_bonus_under_100=_d("0"),
            main_bonus_at_100=_d("200"),
            main_bonus_at_120=_d("400"),
            main_progress_under_80=_d("0.80"),
            main_progress_under_100=_d("1.00"),
            main_progress_under_120=_d("1.20"),
            extra_fixed_pay=_d("150"),
            extra_other_threshold=_d("0.79"),
            extra_other_rate=_d("0.03"),
            sim_unit_rate=_d("3"),
            epay_under_50_rate=_d("5"),
            epay_at_or_over_50_rate=_d("12"),
            flip_default=_d("0"),
            salary_default=_d("0"),
            ticket_default=_d("0"),
            incentive_default=_d("0"),
            rounding="ROUND_HALF_UP",
        )

    def to_canonical_dict(self) -> dict[str, Any]:
        """Stable JSON-friendly view; Decimal serialised as string."""

        out: dict[str, Any] = {}
        for field in self.__slots__:
            value = getattr(self, field)
            if isinstance(value, Decimal):
                out[field] = format(value, "f")
            else:
                out[field] = value
        return out

    def canonical_hash(self) -> str:
        """SHA-256 over the canonical JSON of the pack."""

        payload = json.dumps(
            self.to_canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_default_rule_pack() -> RulePackV1:
    """Singleton accessor — keeps callers from instantiating new copies."""

    return RulePackV1.default()


def rounding_mode() -> str:
    """Return the rounding mode used by the engine.

    The contract (docs/MOBIUP_RULE_PACK.md §6) requires ``ROUND_HALF_UP`` for
    positive values; we document it here so every component agrees.
    """

    return ROUND_HALF_UP.__str__()


def money(value: Decimal) -> Decimal:
    """Round a Decimal value to whole RON using ROUND_HALF_UP (per contract)."""

    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def quantize_amount(value: Decimal) -> Decimal:
    """Round a monetary value to two decimals (RON cents), ROUND_HALF_UP."""

    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def hash_inputs(payload: dict[str, Any]) -> str:
    """Compute the canonical inputs hash.

    The same ``payload`` (built from the calendar revision, sales generation,
    salary snapshot, Pontaj summary, Epay observations) must always produce
    the same hash. Decimal values are serialised as their ``str`` so the
    Python ``Decimal`` repr never leaks.
    """

    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_default_json,
        ).encode("utf-8")
    ).hexdigest()


def _default_json(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"unsupported type for inputs hash: {type(obj).__name__}")


@dataclass(frozen=True, slots=True)
class RulePackParameters:
    """Per-person rule pack inputs sourced from HR/payroll master.

    Salary and tickets come from the effective-dated salary master and cannot
    be edited by a TL. The Flip adjustment is an explicit input; the rule pack
    defaults ``flip`` to zero so a forgotten snapshot is auditable rather than
    silently substituted.
    """

    salary: Decimal
    tickets: Decimal
    flip: Decimal
    incentive: Decimal

    @classmethod
    def zero(cls) -> RulePackParameters:
        return cls(
            salary=Decimal("0"),
            tickets=Decimal("0"),
            flip=Decimal("0"),
            incentive=Decimal("0"),
        )


@dataclass(frozen=True, slots=True)
class EpayObservationSnapshot:
    """Two-category E-pay observation per person.

    Empty observations decode to ``quantity=0`` and ``value=0``; the engine
    treats them as valid silent absences rather than blockers. Invalid raw
    values (blank, fraction, negative, >10) are filtered upstream; the engine
    never sees them.
    """

    under_50_quantity: int
    at_or_over_50_quantity: int

    def __post_init__(self) -> None:
        if self.under_50_quantity < 0 or self.under_50_quantity > 10:
            raise ValueError("under_50 quantity must be 0..10")
        if self.at_or_over_50_quantity < 0 or self.at_or_over_50_quantity > 10:
            raise ValueError("at_or_over_50 quantity must be 0..10")

    @classmethod
    def empty(cls) -> EpayObservationSnapshot:
        return cls(under_50_quantity=0, at_or_over_50_quantity=0)


@dataclass(frozen=True, slots=True)
class PontajHoursSnapshot:
    """Per-person Pontaj summary used by the grid engine."""

    working_days: int
    working_hours: Decimal
    leave_days: int
    off_days: int


__all__ = [
    "EpayObservationSnapshot",
    "PontajHoursSnapshot",
    "RULE_PACK_VERSION",
    "RulePackParameters",
    "RulePackV1",
    "get_default_rule_pack",
    "hash_inputs",
    "money",
    "quantize_amount",
    "rounding_mode",
]
