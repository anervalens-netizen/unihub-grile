# UGR-001 S2 evidence

Candidate implementation commit: `cfba078fe9d129db6bfa7c6bf84f2fe98ba865b7`

The preceding S2 implementation commit was `43481a4de46df4d0e4f4aa654de3aec9e7b6c9ae`;
`cfba078` closes the independent MiniMax audit findings without changing the
stage boundary.

This evidence is sanitized and contains no employee records, credentials, live
Google identifiers, Retail data, or screenshots.

## Scope

- Revisioned calendar CAS apply with closed-month rejection.
- Effective-dated tenant-safe manager scopes with deny-by-default writes and
  read filtering.
- `NORMAL`, `EXTRA_HOME`, `EXTRA_OTHER`, `OFF`, and `LEAVE` validation.
- Person-calendar, store-coverage, and configurable-hours Pontaj projections.
- XLSX template/Manifest, hidden technical IDs, protected `_Lists`, unlocked
  day dropdown cells, per-person valid choices, round-trip parser, preview and
  atomic apply.

## Verification

- `test-summary.txt`: backend regression and S2 API/domain/service tests,
  `49 passed`.
- `typecheck-summary.txt`: `ruff check` and `mypy --strict` clean.
- `postgres-migration.txt`: fresh PostgreSQL 17 migration upgrade and
  `alembic check` clean; head `c3a1b7e2d4f6`, `manager_scopes` present.
- `frontend-environment.txt`: existing frontend checks remain blocked by the
  pre-existing root-owned pnpm store and missing optional Vite/Rollup packages;
  no frontend files changed in S2.

The exact-commit re-audit is the formal S2 gate; this directory records the
builder-side replay and sanitized observations only.
