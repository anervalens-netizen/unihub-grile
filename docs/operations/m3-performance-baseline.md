# M3 performance baseline

This document is the reproducible performance contract for `BE-003`, `BE-004`, `BE-005`, and the profiled read-path work in `BE-007`. It records both the original calibration and the accepted optimized state.

## Dataset

`backend/tests/fixtures/performance.py` creates one disposable PostgreSQL tenant with:

- 80 active stores;
- 160 active people (two per store);
- 31 days of August 2026 calendar data, one working person per store/day;
- 2,480 daily sales rows and 2,480 Pontaj rows;
- monthly store targets;
- valid E-pay observations;
- materialized sales attribution;
- 160 current-revision grid snapshots;
- one persisted last-good fake Sheet projection for the representative store.

Fixture construction is outside the measured latency window.

## Measured reads

The CI step `M3 performance contract` runs `tests/integration/test_m3_performance.py` on PostgreSQL 17 and prints `M3_PERF` samples. The Store screen sample deliberately measures the eight GETs currently composed by `frontend/src/pages/Magazin.tsx` as one screen-level budget.

Original calibration: Backend CI run `32584789529` on head `8d1c69a16bbbac6ddb0bc45e3f1f9ed6072b4bcf`.
Optimized calibration: Backend CI run `32586557983` on head `6fc506e2f3e8163fc0fce0be19f8e7cb9583285e`.

| Read path | Baseline SELECTs | Optimized SELECTs | Baseline latency | Optimized observed latency | Accepted query budget | CI latency ceiling |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Overview | 21 | 21 | 204.48 ms | 317.50 ms | 21 | 2,000 ms |
| Program, stores perspective | 6 | 6 | 776.92 ms | 82.23 ms | 6 | 4,000 ms |
| Grid | 35 | 5 | 20.91 ms | 13.45 ms | 5 | 1,000 ms |
| Store screen, 8-request bundle | 163 | 73 | 1,823.23 ms | 507.77 ms | 73 | 8,000 ms |

Query counts are the deterministic regression contract: an additional SQL round-trip fails CI. Latency is also checked with deliberately wide shared-runner headroom; individual observed latency values are evidence rather than a microbenchmark promise.

## Accepted BE-007 improvements

### Program matrix construction

The original Program builder repeatedly scanned the complete assignment collection for every store/day or person/day cell, and the people perspective also rescanned absences. The accepted builder creates assignment-by-store/date, assignment-by-person/date, and absence indexes once, then performs O(1) cell lookups.

The optimization preserves the existing response contract. A dedicated equivalence test compares the indexed builder to the legacy builder for both perspectives. `setdefault` preserves the legacy first-row behavior if inconsistent duplicate rows are present; conflict detection remains owned by the validation/exception paths.

Observed Program latency fell from 776.92 ms to 82.23 ms on the fixed 80-store/160-person PostgreSQL fixture without increasing query count.

### Admin month scope

`month_store_ids()` previously evaluated `effective_store_ids()` once for every day of the month. For ADMIN, that executed the same tenant-wide store query 31 times although admin scope is date-independent. ADMIN now resolves tenant-wide scope once; MANAGER continues to evaluate the full effective-dated daily union unchanged.

This reduced Grid from 35 to 5 SELECTs and the complete Store-screen bundle from 163 to 73 SELECTs. Store-screen observed latency fell from 1.82 s to about 0.51 s in the calibration run.

## Remaining performance questions

The Store screen still composes eight HTTP reads and remains the largest query bundle. Some endpoints return tenant/month-wide data that the frontend then filters to a store. Further payload or endpoint consolidation should only be implemented if new profiling shows sufficient benefit and scope semantics remain explicit.

`BE-006` is evaluated separately because calendar save is a write-path correctness problem, not a read-path optimization. The current save creates a complete new revision snapshot for calendar/Pontaj and rebuilds attribution; incremental materialization must not silently weaken revision/CAS or historical snapshot invariants.

## Guardrails for optimization

Future performance changes must preserve:

- effective-dated manager scope and historical redaction;
- month revision/CAS semantics;
- current rule-pack/revision filtering for grid rows;
- deterministic attribution and Pontaj outputs;
- existing backend and frontend regression suites.

Any optimization that changes one of these contracts needs correctness evidence first; lower latency alone is not sufficient.
