# M3 performance baseline

This document is the reproducible performance contract for `BE-003`, `BE-004`, `BE-005`, `BE-006`, and the profiled read-path work in `BE-007`. It records both the original calibration and the accepted optimized state, plus later mounted-screen changes that must continue to honor the same anti-N+1 guardrail.

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

The CI step `M3 performance contract` runs `tests/integration/test_m3_performance.py` on PostgreSQL 17 and prints `M3_PERF` samples. The Store screen sample deliberately measures the GETs currently mounted by `frontend/src/pages/Magazin.tsx` as one screen-level budget.

Original calibration: Backend CI run `32584789529` on head `8d1c69a16bbbac6ddb0bc45e3f1f9ed6072b4bcf`.
Final M3 calibration before contract freeze: Backend CI run `32589115250` on head `911ae5b6256caa940dcd794695f5fc6cfa94b6b8`.

| Read path | Baseline SELECTs | Final M3 SELECTs | Baseline latency | Final M3 observed latency | Current query budget | CI latency ceiling |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Overview | 21 | 21 | 204.48 ms | 260.91 ms | 21 | 2,000 ms |
| Program, stores perspective | 6 | 6 | 776.92 ms | 155.36 ms | 6 | 4,000 ms |
| Grid | 35 | 5 | 20.91 ms | 14.46 ms | 5 | 1,000 ms |
| Store screen | 163 | 43 (8 reads) | 1,823.23 ms | 575.99 ms | 45 (9 reads) | 8,000 ms |

Query counts are the deterministic regression contract: an additional SQL round-trip fails CI. Latency is also checked with deliberately wide shared-runner headroom; individual observed latency values are evidence rather than a microbenchmark promise.

### M4 Store screen extension

FE-005 adds the already-authorized durable job diagnostics read to Store Detail so Sheet/export state is visible locally. The mounted ADMIN Store screen therefore grows from eight to nine GETs. The diagnostics route performs exactly two bounded SQL reads for ADMIN (`active` queue rows + bounded terminal history) and does not perform per-job resource lookups. The deterministic Store-screen budget is consequently `43 + 2 = 45 SELECTs`; this is a product-visible read addition, not a relaxation for unexplained regression.

The diagnostics request is loaded separately and fail-soft in the frontend. A diagnostics/provider-status failure therefore does not invalidate the original eight-read core Store Detail bundle.

## Accepted BE-007 improvements

### Program matrix construction

The original Program builder repeatedly scanned the complete assignment collection for every store/day or person/day cell, and the people perspective also rescanned absences. The accepted builder creates assignment-by-store/date, assignment-by-person/date, and absence indexes once, then performs O(1) cell lookups.

The optimization preserves the existing response contract. A dedicated equivalence test compares the indexed builder to the legacy builder for both perspectives. `setdefault` preserves the legacy first-row behavior if inconsistent duplicate rows are present; conflict detection remains owned by the validation/exception paths.

Across calibration runs, Program fell from the original 776.92 ms to roughly 82–155 ms on the fixed 80-store/160-person PostgreSQL fixture without increasing query count.

### Admin month scope

`month_store_ids()` previously evaluated `effective_store_ids()` once for every day of the month. For ADMIN, that executed the same tenant-wide store query 31 times although admin scope is date-independent. ADMIN read paths now resolve tenant-wide scope once; MANAGER continues to evaluate the full effective-dated daily union unchanged.

This reduced Grid from 35 to 5 SELECTs. The later BE-006 write-path sweep found the same repeated ADMIN lookup in the manager UI month-date helper and collapsed it there as well. With both semantics-preserving fast paths in place, the original eight-read Store-screen bundle is 43 SELECTs versus 163 at baseline.

## Accepted BE-006 calendar-save result

`backend/tests/integration/test_m3_calendar_save_evaluation.py` executes the real one-cell `POST /months/{id}/program/cell` path against the same PostgreSQL dataset. It changes one working person/day to `OFF` and verifies the resulting revision, calendar state, complete Pontaj snapshot, attribution rebuild, prior-revision preservation, and transactional audit evidence.

The first measurement exposed SQL write fan-out:

| One-cell save | Initial sample | Accepted sample | Regression budget |
| --- | ---: | ---: | ---: |
| SELECTs | 55 | 20 | 20 |
| Total SQL statements | 5,025 | 29 | 29 |
| Observed latency | 2,839.71 ms | 3,423.80 ms | 8,000 ms CI ceiling |

The fix deliberately does **not** replace complete revision snapshots with sparse patches. Calendar authoritative rows and attribution are bulk-persisted, Pontaj is derived once and reused for persistence plus the API result, and PostgreSQL materializes the complete Pontaj revision in bounded multi-value inserts. SQLite retains its efficient in-process executemany path so the existing S4 save p95 contract remains below 500 ms; the final full regression suite passed without relaxing that threshold.

The deterministic improvement is the removal of database round-trip fan-out: 5,025 statements became 29 while the month revision/CAS, complete historical Pontaj lattice, append-only prior revisions, attribution rebuild, and audit transaction remain intact. Raw PostgreSQL wall time is intentionally not treated as a microbenchmark guarantee on shared CI runners.

`BE-006` therefore does not justify a sparse/incremental revision redesign at this stage. Such a redesign would weaken a valuable correctness invariant for limited proven benefit. If interactive save latency later becomes a product issue, the next step is targeted CPU/serialization or database-write profiling while retaining complete revision semantics unless evidence proves otherwise.

## Remaining performance questions

The Store screen now composes nine HTTP reads for ADMIN, with job diagnostics loaded independently from the eight core reads. Some core endpoints return tenant/month-wide data that the frontend then filters to a store. Further payload or endpoint consolidation should only be implemented if new profiling shows sufficient benefit and scope semantics remain explicit.

## Guardrails for optimization

Future performance changes must preserve:

- effective-dated manager scope and historical redaction;
- month revision/CAS semantics;
- complete immutable Pontaj revision history unless a separately approved contract replaces it;
- current rule-pack/revision filtering for grid rows;
- deterministic attribution and Pontaj outputs;
- existing backend and frontend regression suites.

Any optimization that changes one of these contracts needs correctness evidence first; lower latency alone is not sufficient.
