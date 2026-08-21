# Mobiup rule pack — grilă și Pontaj

Status: **canonical Mobiup compatibility contract**  
Initial version: `mobiup-v1-compat`

This document defines Mobiup-specific payroll/Pontaj behavior only. It does not
define project status, architecture or roadmap; those are controlled by issues
#3/#4 and the canonical architecture/product documents.

Sources used to establish compatibility were legacy V1, the Retail V2 pilot and
accepted visual/business references. Those sources are evidence, not runtime
dependencies.

Mobiup rules must remain in a versioned rule pack and must not be scattered
through the generic UniHub Grile domain/API.

## 1. Authoritative inputs

- Grile calendar decides person, store and working classification for each day.
- Connector snapshot supplies physical store/day sales, store monthly target,
  authoritative selling-day divisor, SIM quantity and external incentive inputs.
- Effective-dated HR/payroll master supplies fixed salary, meal tickets and the
  accepted Flip adjustment state/value.
- Google Sheet supplies only validated E-pay quantities.
- Stable IDs are used for identity; names/POS/TL labels are presentation or
  provenance only.

Missing payroll-significant data is an explicit anomaly. A preview may have a
documented fallback where the implementation contract permits one, but a final
close cannot silently treat an unknown required value as authoritative zero.

Observed legacy fixed salary values such as `2400`/`2600 RON` and ticket value
`480 RON` are compatibility evidence, not hardcoded allowed-value lists.

## 2. Target and attribution

For a store:

```text
target_day_store = monthly_store_target / store_sales_days
```

`store_sales_days` is the authoritative source divisor.

The person's main target is the sum of daily targets for days worked in the home
store as `NORMAL` or `EXTRA_HOME`.

`EXTRA_OTHER` uses the host-store daily target and remains separate from the home
main target/result.

The entire physical store/day sale is credited to the person selected by the
calendar. Reassignment changes personal credit only; store/company physical
totals do not change or duplicate.

If the authoritative selling-day divisor is missing, emit an explicit anomaly.
Any preview fallback must remain visible and must be classified correctly by the
close policy.

## 3. Main commission

```text
progress = realised_main / target_main
```

If target is zero, commission/progress behavior follows the deterministic engine
contract and a data anomaly is emitted; no infinite percentage is produced.

| Progress | Main commission |
|---|---:|
| `< 80%` | `0` |
| `>= 80%` and `< 100%` | `3% × realised_main` |
| `>= 100%` and `< 120%` | `3% × realised_main + 200 RON` |
| `>= 120%` | `3% × realised_main + 400 RON` |

Main commission is rounded to whole RON using the rule-pack rounding policy.

## 4. Extra days

Every `EXTRA_HOME` or `EXTRA_OTHER` day adds:

```text
150 RON / day
```

### EXTRA_HOME

- store must equal home store;
- day sale and target enter the main home result/target;
- there is no second percentage commission on the same sale;
- the fixed extra-day pay remains a separate component.

### EXTRA_OTHER

- store must differ from home store;
- host-store sale and target are evaluated separately;
- if `realised_day / target_day >= 0.79`, commission is `3% × realised_day`;
- below `0.79`, percentage commission for that extra-other day is zero;
- each extra-other percentage commission is rounded individually to whole RON,
  then summed;
- fixed `150 RON` and percentage commission are separate auditable components.

The `0.79` threshold is an intentional legacy compatibility rule and must be
covered by golden edge tests.

## 5. SIM, E-pay and incentive

| Component | Rule |
|---|---:|
| Eligible SIM | `3 RON × quantity` |
| E-pay `<50 lei` | `5 RON × quantity` |
| E-pay `>=50 lei` | `12 RON × quantity` |
| Monthly incentive | authoritative external input |

E-pay quantity is integer `0..10` per category/person. Invalid observations are
audited and do not erase last-good.

SIM is not manually entered through Sheet. Incentive is an external authoritative
input and is not reverse-engineered from the grid.

If this rule pack is active, the final close requires the expected E-pay input
set to be valid and fresh under the active `ClosePolicy`.

## 6. Salary total

The engine stores components separately and computes:

```text
total_salary = fixed_salary
             + meal_tickets
             + main_commission
             + extra_fixed_pay
             + extra_other_commission
             + sim_commission
             + epay_commission
             + monthly_incentive
             + Flip_adjustment_if_active

salary_cash = total_salary - meal_tickets
```

`total_salary` includes meal tickets.

Compatibility example:

```text
2600 + 480 + 27 SIM + 350 incentive = 3457 RON
```

when all other components are zero.

All monetary calculations use `Decimal`. Compatibility with the accepted Google
`ROUND(...,0)` behavior uses `ROUND_HALF_UP` for positive values unless a later
versioned rule explicitly changes it.

Persisted calculation evidence includes:
- canonical inputs;
- raw/rounded components where needed;
- rule-pack version/hash;
- revision/source generation;
- input/output hashes;
- anomalies.

## 7. Pontaj standard Mobiup

Pontaj is a read-only projection of the calendar. It is not a second attendance
authority.

### Fixed `Pontaj` layout

- days `1..31` are Excel columns `C:AG`;
- column `AH` contains monthly net-hours total;
- compatibility area is `C8:AG31`;
- eight potential three-row blocks start at rows `8, 11, 14, 17, 20, 23, 26, 29`;
- standard two-person store uses blocks starting at rows `8` and `11`;
- for block start row `r`:
  - `r` = net hours;
  - `r+1` = interval;
  - `r+2` = break;
- monthly total is `AHr = SUM(Cr:AGr)`.

### Daily projection

| Calendar state | Net hours | Interval | Break |
|---|---:|---|---:|
| `NORMAL` | `11` | `10:00-22:00` | `1` |
| `EXTRA_HOME` | `11` | `10:00-22:00` | `1` |
| `EXTRA_OTHER` | `11` | `10:00-22:00` | `1` |
| `OFF` | blank | blank | blank |
| `LEAVE` | blank | blank | blank |
| non-existent date in month | blank | blank | blank |

Weekends are visually highlighted according to the selected month. Weekend
formatting does not change hours/pay by itself.

The standard policy is:
- interval `10:00–22:00`;
- break `60 minutes`;
- net `11 hours`.

A calendar change rebuilds/updates the authoritative Pontaj projection for the
same business revision. Manual Sheet changes are not preserved as attendance
truth.

## 8. Holidays

Romanian legal holidays are stored/versioned with an administrative override
mechanism.

For `mobiup-v1-compat` their initial effect is **informational only**:
- they may be displayed/marked;
- they do not automatically change schedule;
- they do not automatically change Pontaj;
- they do not automatically change target or pay.

A different effect requires a new explicit/versioned business decision, not an
implicit code change.

## 9. Salary master and Flip

- fixed salary and meal tickets come from an effective-dated master snapshot;
- manager schedule UI does not edit these values;
- missing required master input is an explicit payroll anomaly;
- accepted legacy `Flip` adjustment remains a separately represented versioned
  calculation component.

## 10. Required golden/edge fixtures

The candidate suite must include at least:

1. main progress `79.99%`, `80%`, `99.99%`, `100%`, `119.99%`, `120%`;
2. `EXTRA_HOME` fixed pay with no duplicate percentage commission;
3. `EXTRA_OTHER` below `0.79`, exactly `0.79`, above threshold;
4. personal reassignment with unchanged physical store/company total;
5. SIM at representative edge quantities;
6. both E-pay categories at `0`, `1`, `10` plus invalid observations;
7. example `2600 + 480 + 27 + 350 = 3457`;
8. mid-month schedule change and Pontaj reconstruction;
9. target zero;
10. missing sale;
11. missing selling-day divisor;
12. missing salary master;
13. closed-month mutation rejection;
14. identical canonical input producing identical result/hash.

## 11. Close classification

This document identifies financial significance; the versioned close policy owns
the final blocker classification.

At minimum, the candidate must not close as financially valid when a required
payroll input is unknown/stale/unvalidated. This includes expected E-pay when the
active rule pack uses it and required salary/master/source generation inputs.

Warnings that do not affect payroll determinism may remain non-blocking if the
policy states so explicitly.

## 12. Versioning

Any intentional change to formulas, thresholds, rounding, Pontaj policy or
financial input semantics requires:
- a new rule-pack version or explicit backward-compatible versioned change;
- updated canonical hash;
- updated golden tests;
- migration/reconciliation impact assessment;
- tracker/contract update.

Do not silently change `mobiup-v1-compat` behavior to make a failing historical
example pass.
