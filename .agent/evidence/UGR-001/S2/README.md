# UGR-001 S2 evidence

Candidate implementation commit: `9d8caffaf0df39ff2364c44df673ae05a812e7f4`

The commit closes the three previously recorded S2 NO-GO defects:
server-issued XLSX contracts, date-specific partial-month scope, and persistent
full-month Pontaj projections.

This evidence is sanitized and contains no employee records, credentials, live
Google identifiers, Retail data, or screenshots.

## Scope

- Revisioned calendar CAS apply with closed-month rejection.
- Effective-dated tenant-safe manager scopes with deny-by-default writes and
  read filtering.
- `NORMAL`, `EXTRA_HOME`, `EXTRA_OTHER`, `OFF`, and `LEAVE` validation.
- Persistent full active-person × every-day Pontaj projections, immutable by
  calendar revision, readable with per-person totals.
- XLSX templates with protected Manifest and technical lists, server-issued
  single-use contract token, date-specific dropdowns, locked out-of-scope
  `BLOCAT` cells, formula/merged-cell rejection, preview and atomic apply.

## Verification

- `test-summary.txt`: exact-commit backend regression and S2 coverage,
  `86 passed, 1 warning`.
- `typecheck-summary.txt`: Ruff, mypy strict on 42 source files, and diff check
  clean.
- `postgres-migration.txt`: fresh PostgreSQL 17 migration through
  `d4e6f8a0b2c4`, `alembic check` clean, and 6 integration tests passed.
- `api-smoke-summary.txt`: fresh PostgreSQL end-to-end API smoke covering
  template/preview/apply, 93 Pontaj rows, replay, Manifest tamper, partial
  scope and blocked-cell tamper.
- `luna-final-audit.txt`: explicit GPT-5.6 Luna via provider `openai`, exact-SHA
  read-only GO/PASS for AC-04, AC-05, AC-06 and relevant AC-11.
- `frontend-environment.txt`: existing frontend checks remain environment-limited;
  no frontend files changed in S2.

The formal stage gate is the explicit exact-commit Luna audit recorded in
`luna-final-audit.txt`.
