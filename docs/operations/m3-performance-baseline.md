# M3 performance baseline

This document is the reproducible baseline for `BE-003`, `BE-004`, and `BE-005`.
It is intentionally a measurement contract, not a claim that the current read paths are already optimized.

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

Calibration sample: Backend CI run `32584789529`, branch head `8d1c69a16bbbac6ddb0bc45e3f1f9ed6072b4bcf`.

| Read path | SELECTs | Observed latency | Query budget | CI latency ceiling |
| --- | ---: | ---: | ---: | ---: |
| Overview | 21 | 204.48 ms | 21 | 2,000 ms |
| Program, stores perspective | 6 | 776.92 ms | 6 | 4,000 ms |
| Grid | 35 | 20.91 ms | 35 | 1,000 ms |
| Store screen, 8-request bundle | 163 | 1,823.23 ms | 163 | 8,000 ms |

Query counts are the deterministic regression contract: an additional SQL round-trip fails CI. Latency is also checked, but with deliberately wide shared-runner headroom; the observed `M3_PERF` lines are the useful latency evidence.

## What the baseline says

The Program request is SQL-bounded at six SELECTs but is much slower than Grid. Code inspection shows the current matrix builder repeatedly scans the complete assignment collection while constructing each cell, so the next optimization pass should target in-memory indexing before adding SQL complexity.

Grid is fast in elapsed time but spends 35 SELECTs on an admin request. The authorization helper `month_store_ids()` currently evaluates store visibility once for every day of the month; for an admin that repeats an identical tenant-wide store lookup. This is a strong candidate for a semantics-preserving admin fast path.

The Store screen is the largest current cost at 163 SELECTs and 1.82 s. It composes eight HTTP reads, including tenant-wide Attribution and Grid responses that the frontend subsequently filters to one store. Follow-up work should reduce duplicate authorization lookups and request/store payload fan-out before considering caching.

## Guardrails for optimization

Future performance changes must preserve:

- effective-dated manager scope and historical redaction;
- month revision/CAS semantics;
- current rule-pack/revision filtering for grid rows;
- deterministic attribution and Pontaj outputs;
- existing backend and frontend regression suites.

Any optimization that changes one of these contracts needs correctness evidence first; lower latency alone is not sufficient.
