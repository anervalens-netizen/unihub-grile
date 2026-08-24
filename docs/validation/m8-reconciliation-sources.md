# M8 reconciliation source selection

Status: accepted source-selection evidence for `VAL-001` / `VAL-002`.

## Purpose

M8 reconciles UniHub Grile against trustworthy prior outputs without
publishing real employee identities, Google resource identifiers, or private
business workbooks in the repository.

The source workbooks remain read-only in the private/connected source system.
Only aggregate/anonymized evidence belongs in Git history.

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
later M8 gates. Those tags are scenario inventory, not evidence that the pure
grid function alone proves DB/service behavior.

## VAL-002 accepted source families and periods

### Historical V1 / legacy source

Primary reconciliation baseline: **2026-07-31 month-end**.

Selection evidence:

- the legacy operational workbook family contains `Grila` and `Pontaj` tabs;
- the selected Pontaj validation subset contains eight distinct store
  workbooks with an exact 2026-07-31 revision before the August reset/import;
- all eight selected July sources contain real worked-hour evidence rather
  than a default zero-only Pontaj;
- populated worked-day cells consistently represent 11 net hours;
- where interval/pause rows are materialized, they can be reconciled as source
  presentation/configuration rather than inferred from blanks.

Secondary historical-period evidence: **2026-06-30 month-end**.

A fully populated legacy month-end snapshot was independently inspected for
June. It contains target, realised sales, commission, total salary, worked-day
information and Pontaj evidence. This establishes that the historical source
selection is not based on a single month only.

Important distinction: earlier spot revisions inspected at **2026-05-31** and
**2026-06-10** were not sufficiently populated and remain excluded. Their
exclusion does not invalidate the later, fully populated 2026-06-30 month-end
snapshot.

### V2 pilot source

Accepted period: **2026-08**.

Selection evidence:

- the connected source contains more than eight distinct `PILOT V2` store
  workbooks for the month;
- the grid/payroll reconciliation cohort uses eight distinct stores and two
  mapped participants per store (16 participants total);
- the inspected `Rezumat & Program` outputs contain target, realised sales,
  salary components and calculated totals suitable for comparison;
- the current V2 `Pontaj` tabs were not materialized reliably enough to serve
  as an independent Pontaj oracle, so they are deliberately excluded from the
  Pontaj comparison rather than treated as zero.

The independent Pontaj comparison therefore uses the qualified V1 July
month-end subset, while the V2 August pilot family is used for grid/payroll.
This source split is intentional: each surface is compared only against a
source that actually materializes that surface.

For the public repository the stores and people are referenced only as
anonymized cohort members. Exact source file IDs, revision IDs, employee names
and store names are intentionally not committed.

## Reliability rules for VAL-003 / VAL-004

A source output is eligible only when all of the following are true:

1. period and source version are identifiable;
2. target and realised values are populated rather than default/blank;
3. output components required for the comparison are present;
4. person/store rows can be mapped to an anonymized cohort identity without
   committing the original identifier;
5. the source snapshot is read-only during reconciliation;
6. uncertainty is recorded as `source uncertainty`, never silently converted
   to zero or treated as a Grile defect.

Every reconciliation difference must enter the mismatch ledger with:

- anonymized scope/period reference;
- component;
- observed delta or representation difference;
- root cause;
- payroll impact;
- disposition (`legacy/source defect`, `intentional contract behavior`,
  `rounding/version`, `Grile defect`, or `source uncertainty`).

`VAL-005` cannot pass until unexplained payroll-impacting mismatches are zero.

## Boundaries

- No UniHub Retail mutation is part of this validation.
- No source Google Sheet is modified by this validation.
- No real employee identity or Google spreadsheet/resource ID belongs in Git
  history.
- Synthetic VAL-001 results do not certify historical reconciliation; the
  historical certification is recorded separately in the M8 reconciliation
  report.
