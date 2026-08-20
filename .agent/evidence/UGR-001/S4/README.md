# S4 evidence index

Files in this directory back the Stage S4 (Manager UI) handoff. They are
sanitised — no credentials, no production rows, no Google Sheet IDs.

## `test-summary.txt`
234 pytest passed, 1 SQLite-only skip (idempotency key on PostgreSQL).
Ruff clean, mypy --strict clean on 58 source files. S4 adds 28 new tests
(overview/program/exceptions/close/perf/query-count + worker jobs).

## `typecheck-summary.txt`
Backend mypy --strict and frontend tsc --noEmit are both clean. The
frontend tsc required a one-shot symlink workaround documented in
BLOCKERS (the root-owned pnpm store cannot install optional native
binaries; a single @rollup/rollup-linux-x64-gnu binary was placed in
the user-writable top-level node_modules to satisfy Vite).

## `perf.txt`
Offline benchmark against an in-memory SQLite seeded with 80 stores
and a 5-day calendar across 31 days. p95 well under budget:
- overview   p95 = 2.6ms  (budget 1000ms)
- program    p95 = 2.0ms  (budget 1000ms)
- exceptions p95 = 2.9ms  (budget 1000ms)
- save       p95 = 295ms  (budget  500ms)

The benchmark is wired to run automatically inside pytest with
`UGR_S4_PERF_ITER=N` (default 12). As of the S4 perf-sink repair
(`fix(s4): route perf evidence to non-tracked path so verifier tree
stays clean`), the benchmark no longer writes to this tracked file —
it routes its human-readable summary to a non-tracked path so the
verifier's mandatory clean post-attestation is preserved. Control:

- default sink: `tempfile.gettempdir() / ugrile-s4-perf.txt`
- explicit sink: `UGR_S4_PERF_OUT=/some/path pytest tests/api/test_s4_perf.py`
- opt-out (CI): `UGR_S4_PERF_OUT="" pytest tests/api/test_s4_perf.py`

The historical snapshot committed at the S4 candidate stays in place
as the audit record.

## `index.html`
Production-built `dist/index.html` artefact produced by `vite build`.
The bundle is 162.48 kB (51.47 kB gzipped); the full set of S4 routes
(Overview / Program / Magazin / Agent / Excepții / Close) compiles
without warnings.

## Alembic migration
`backend/migrations/versions/0008_s4_export_projection_jobs.py` extends
the `outbox_jobs.kind` check constraint so the four S4 job kinds
(EXPORT_XLSX_STORE / EXPORT_XLSX_BULK / EXPORT_PONTAJ_ONLY /
GOOGLE_PROJECTION_STORE) are accepted. The migration lands on a
fresh PostgreSQL 17 schema with `alembic check` reporting zero drift.
