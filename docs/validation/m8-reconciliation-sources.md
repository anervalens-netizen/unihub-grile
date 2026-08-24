# M8 reconciliation source selection

Status: source-selection evidence for `VAL-001` / `VAL-002`.

## Purpose

M8 must reconcile UniHub Grile against trustworthy prior outputs without
publishing real employee identities, Google resource identifiers, or private
business workbooks in the repository.

This document records the selection method and the periods accepted for the
validation run. The source workbooks themselves remain read-only in the
private/connected source system.

## VAL-001 shadow cohort

The executable cohort lives in:

- `backend/tests/fixtures/m8_shadow_cohort.py`
- `backend/tests/validation/test_m8_shadow_cohort.py`

It contains synthetic identifiers only and represents at least eight stores.
The pure-domain cases lock the documented payroll thresholds and components:

- main progress immediately below / at 80%, 100%, and 120%;
- `EXTRA_HOME` fixed pay without double commission;
- `EXTRA_OTHER` below and at the 0.79 threshold;
- SIM, E-pay, incentive and the V2 `3457` example;
- target-zero behavior;
- deterministic repeated calculation.

The cohort also reserves explicit scenarios for service-layer validation in
`VAL-003`: missing authoritative inputs, mid-month change, Pontaj rebuild and
reassignment/company-total invariance. Those tags are **scenario inventory**,
not evidence that the pure grid function alone proves the DB/service behavior.

## VAL-002 accepted source families

### Historical V1 / legacy source

Accepted period: **2026-07 month-end**.

Selection evidence:

- the legacy operational workbook family contains `Grila` and `Pontaj` tabs;
- Drive revision history contains month-end snapshots across several months;
- the **2026-07-31** revision inspected for the selected source is populated,
  including per-person target, realised sales, worked-day count, commission,
  total salary and Pontaj rows;
- earlier revisions inspected for **2026-05-31** and **2026-06-10** were not
  populated enough to be trustworthy reconciliation baselines and are
  deliberately excluded.

The July month-end revision is therefore the first currently accepted V1
historical baseline. If additional month-end workbooks are found with the same
quality, they may be added later; they must not be inferred from an empty or
partially populated revision.

### V2 pilot source

Accepted period: **2026-08**.

Selection evidence:

- the connected source contains more than eight distinct `PILOT V2` store
  workbooks for the month;
- inspected workbooks expose a structured `Rezumat & Program` tab together
  with `Pontaj`, sales/incentive inputs and calculated salary components;
- these are treated as V2 pilot comparison outputs, not as a substitute for
  historical V1 month-end evidence.

For the public repository the stores are referenced only as anonymized cohort
members. Exact source file IDs, employee names and store names are intentionally
not committed.

## Reliability rules for VAL-003 / VAL-004

A source output is eligible only when all of the following are true:

1. period and source version are identifiable;
2. target and realised values are populated rather than default/blank;
3. payroll output components required for the comparison are present;
4. person/store rows can be mapped to an anonymized cohort identity without
   committing the original identifier;
5. the source snapshot is read-only during reconciliation;
6. uncertainty is recorded as `source uncertainty`, never silently converted
   to zero or treated as a Grile defect.

Every reconciliation difference must enter the mismatch ledger with:

- anonymized store/person/period reference;
- component;
- old result;
- new result;
- delta;
- root cause;
- disposition (`legacy defect`, `intentional contract change`,
  `rounding/version`, `Grile defect`, or `source uncertainty`).

`VAL-005` cannot pass until unexplained payroll-impacting mismatches are zero.

## Boundaries

- No UniHub Retail mutation is part of this validation.
- No source Google Sheet is modified by this plan.
- No real employee identity or Google spreadsheet ID belongs in Git history.
- Synthetic VAL-001 results do not certify historical reconciliation; that
  certification begins only when VAL-003 runs against the selected source
  evidence.
