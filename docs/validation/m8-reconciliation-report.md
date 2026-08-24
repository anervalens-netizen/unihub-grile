# M8 historical reconciliation report

Status: evidence for `VAL-002`, `VAL-003`, `VAL-004`, and `VAL-005`.

Candidate contract inspected: `main` at
`5189de40a6c3a8dbdfdca612c67757adf70bdd8c`.

This report contains only anonymized/aggregate evidence. The historical source
workbooks remain read-only and private; no real employee identity, store name,
Google resource ID, or revision ID is committed here.

## Verdict

- `VAL-002`: **COMPLETE** — reliable historical periods/source families are
  selected and the eligibility rules are recorded.
- `VAL-003`: **COMPLETE (source-qualified split)** — grid/payroll is reconciled
  on an eight-store V2 cohort (16 participants), while Pontaj is independently
  reconciled on an eight-store V1 July month-end subset because the current V2
  Pontaj surface is not reliably materialized.
- `VAL-004`: **COMPLETE** — every observed difference is classified below.
- `VAL-005`: **COMPLETE** — unexplained payroll-impacting mismatches: **0**.

`COMPLETE (source-qualified split)` is deliberate. It does not claim that an
empty V2 Pontaj is a successful comparison. Each surface is validated against
a source snapshot that actually contains the corresponding output.

## Source periods

Accepted evidence:

- V1 historical primary baseline: **2026-07-31 month-end**;
- V1 secondary period confirmation: **2026-06-30 month-end**;
- V2 pilot grid/payroll comparison: **2026-08**.

A source is excluded when required output is blank/default, even if its file or
revision exists. In particular, incomplete spot revisions must not be promoted
to month-end evidence.

## VAL-003 — grid and payroll reconciliation

### Scope

The V2 comparison cohort contains:

- 8 distinct stores;
- 2 mapped participants per store;
- 16 participants total.

The comparison covers the payroll-driving fields exposed by the selected V2
outputs and the `mobiup-v1-compat` contract in the candidate:

- target and realised main sales;
- progress / threshold classification;
- fixed salary and meal tickets;
- main commission and threshold bonus;
- `EXTRA_HOME` fixed pay;
- `EXTRA_OTHER` commission/fixed-pay behavior;
- SIM commission;
- E-pay commission;
- monthly incentive;
- final salary total.

### Result

- Threshold classification agreement: **16 / 16**.
- Raw displayed final-total agreement: **15 / 16**.
- Canonical normalized final-total agreement: **16 / 16**.
- Unexplained payroll-impacting mismatches: **0**.

The sole raw monetary delta is a half-RON incentive/source-total representation.
The candidate contract uses whole-RON `ROUND_HALF_UP` money normalization. Once
the same canonical normalization is applied, the participant total agrees.
This is a source/version rounding difference, not a reason to alter the rule
pack.

Progress values in the pilot source may retain more decimal places than the
candidate's four-decimal progress representation. All 16 mapped pilot cases
remain in the same threshold bucket, so the precision difference has no
commission/bonus consequence in this cohort.

## VAL-003 — Pontaj reconciliation

### Why V1 is the independent oracle

The inspected current V2 pilot `Pontaj` tabs are not reliably materialized even
when the corresponding program/payroll surface is populated. Treating those
blank cells as zero would manufacture a false mismatch (or false PASS).

Pontaj is therefore validated independently against eight exact V1
**2026-07-31 month-end** snapshots selected before the August reset/import.
Two initially inspected July sources whose Pontaj was entirely blank were
rejected and replaced with populated July sources before the cohort was frozen.

### Result

Final Pontaj validation subset:

- qualified stores: **8 / 8**;
- stores containing real worked-hour evidence: **8 / 8**;
- observed populated worked-day net hours: **11 hours consistently**;
- sources materializing interval/pause rows: **5 / 8**;
- sources preserving net hours but omitting interval/pause presentation:
  **3 / 8**.

The candidate's `HoursConfig` intentionally does **not** hardcode an exact
business interval as policy. Its safe default is 10:00–22:00 with a 60-minute
pause, producing 11 net hours, while alternate valid client schedules can
produce the same net hours.

One historical source uses 09:00–21:00 with a one-hour pause; the remaining
materialized interval examples use the 10:00–22:00 family. Both yield the same
11 net hours and are therefore configuration/presentation variants, not a
payroll-hours defect.

Legacy workbooks also use display markers such as leave/off tokens and are not
uniform about repeating interval/pause rows. The reconciliation compares the
payroll-relevant Pontaj semantics (working/non-working state and net hours)
and records missing presentation fields as source-format differences rather
than inventing values.

## VAL-004 — mismatch ledger

| ID | Surface | Observed difference | Root cause | Payroll impact | Disposition |
| --- | --- | --- | --- | --- | --- |
| M8-M01 | Payroll | One participant has a 0.5 RON raw source-total delta | Historical/pilot source retains a half-RON incentive; candidate normalizes money with whole-RON `ROUND_HALF_UP` | None after canonical normalization | `rounding/version`; candidate unchanged |
| M8-M02 | Attribution/grid | One pilot row contains a negative adjustment on a day where the mapped person is not calendar-authoritative | Source attribution drift | No observed payroll impact in the cohort; potentially material near a threshold | `legacy/source defect`; calendar authority retained |
| M8-M03 | V2 Pontaj | Current V2 Pontaj surface is blank/unmaterialized despite populated program/payroll | Source surface not materialized | None used for certification; surface excluded as oracle | `source uncertainty`; independent V1 Pontaj used |
| M8-M04 | V1 Pontaj | Three qualified sources record 11-hour days but omit interval/pause display rows | Legacy presentation inconsistency | None; net hours remain explicit | `legacy/source format`; no engine change |
| M8-M05 | V1 Pontaj | One qualified source uses 09:00–21:00 rather than the 10:00–22:00 default | Client/site schedule variant supported by configurable `HoursConfig` | None; both produce 11 net hours with one-hour pause | `intentional contract behavior` |
| M8-M06 | Grid | Pilot progress values have more display precision than candidate progress | Representation precision | None in cohort; all mapped cases remain in the same threshold bucket | `rounding/version` |

There are no `Grile defect` entries in this ledger.

## VAL-005 gate

`VAL-005` requires zero unexplained payroll-impacting mismatches.

Result:

- observed ledger entries: **6**;
- explained/dispositioned entries: **6 / 6**;
- unexplained entries: **0**;
- unexplained payroll-impacting entries: **0**.

Therefore the `VAL-005` gate passes for the selected reconciliation evidence.

## What this does not certify

This report does not substitute for later adversarial/service-operation gates.
It does not certify:

- authorization and scope attacks (`VAL-006`);
- close/reopen/concurrency/audit-chain behavior on PostgreSQL (`VAL-007`);
- worker crash/retry/recovery behavior (`VAL-008`);
- production deployment or live provider mutation.

## Safety / provenance boundary

- UniHub Retail remained read-only and was not modified.
- Source Google workbooks were read only; no Sheet mutation was performed.
- No deploy, tag, release, or production mutation was performed.
- Private source identifiers and employee/store identities remain outside Git
  history.
