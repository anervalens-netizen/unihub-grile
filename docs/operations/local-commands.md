# UniHub Grile — local development and verification

This document describes the current standalone developer workflow. It does not
authorize production/server deployment and it does not require UniHub Retail.

Canonical plan/status: issues #3 and #4. Runtime variables are inventoried in
`docs/operations/environment.md`; backup/restore/migration safety is defined in
`docs/operations/backup-restore-migrations.md`.

## 1. Prerequisites

Expected local tools:
- Python 3.12 compatible environment;
- Node 20+;
- `pnpm`;
- Docker for the ephemeral PostgreSQL path;
- Git.

Do not use production credentials or live employee/payroll data in the local
fixture workflow.

## 2. Install

From repository root:

```bash
make install
```

This creates the backend virtual environment under `backend/.venv` and installs
the frontend dependencies.

## 3. PostgreSQL

```bash
make pg-up
make migrate
```

The Makefile currently uses a dedicated ephemeral PostgreSQL 17 container on
`127.0.0.1:55432` by default.

Stop it with:

```bash
make pg-down
```

The container names/default credentials are development-only. They must not be
reused as a server/production configuration contract.

## 4. API and worker

API:

```bash
make api
```

Worker in a second terminal:

```bash
cd backend
DATABASE_URL=postgresql+psycopg://grile:grile@127.0.0.1:55432/grile \
  .venv/bin/python -m ugrile.worker.worker
```

The API enqueues asynchronous work; the worker is the execution authority for
outbox jobs. Retry, committed-lease recovery, supersession and operator
diagnostics are part of the completed M2 contract; successful startup still does
not replace the health/readiness and recovery evidence required for server test.

## 5. Frontend

```bash
make web
```

The Vite dev server uses the standalone API proxy configuration.

Local development may use `X-Ugrile-Identity` / `X-Ugrile-Tenant` through the
explicit `dev_headers` principal provider. That provider is rejected by startup
validation when `APP_ENV=prod`.

These headers are **not** the future Retail auth contract and must not be treated
as production security.

## 6. Health/readiness and metrics

With the API running:

```bash
make health
```

or directly:

```bash
curl -fsS http://127.0.0.1:8080/livez   | python3 -m json.tool
curl -fsS http://127.0.0.1:8080/healthz | python3 -m json.tool
curl -fsS http://127.0.0.1:8080/readyz  | python3 -m json.tool
curl -fsS http://127.0.0.1:8080/version | python3 -m json.tool
curl -fsS http://127.0.0.1:8080/metrics
```

Probe semantics are fixed by `docs/operations/observability.md`:

- `/livez` is process-only liveness;
- `/healthz` is an HTTP-200 dependency summary;
- `/readyz` is the fail-closed traffic gate for DB/schema/stale worker leases;
- `/metrics` exposes bounded privacy-safe operational metrics.

## 7. Standard verification

```bash
make lint
make typecheck
make test
make build
```

Formatting when intentionally changing formatting:

```bash
make format
```

Before pushing a PR, prefer targeted tests for the changed area plus the relevant
full commands above. CI should validate, not become the primary iterative
debugger.

## 8. Backend-only commands

```bash
cd backend
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/python -m pytest tests -q
```

For PostgreSQL-marked/integration tests, provide an explicit reachable database
URL as required by the test configuration. Real concurrency/constraint claims
must use PostgreSQL, not only SQLite.

## 9. Frontend-only commands

```bash
cd frontend
pnpm exec tsc --noEmit
pnpm exec vitest run
pnpm run build
```

A build and Vitest PASS do not prove visual/browser correctness. Final UX claims
follow `docs/QUALITY_GATES.md` and the mandatory Browser E2E workflow.

## 10. Fixture ingest

Fixture ingest is for development/test only.

With an appropriate local admin fixture principal:

```bash
curl -fsS -X POST http://127.0.0.1:8080/ingest/fixture \
  -H 'Content-Type: application/json' \
  -H 'X-Ugrile-Identity: user_admin' \
  -H 'X-Ugrile-Tenant: tenant_acme' \
  -d '{"tenant_token":"acme"}' | python3 -m json.tool
```

The endpoint enqueues work; run the worker to settle it. The route is not mounted
when `APP_ENV=prod`.

## 11. Docker Compose

The repository compose file is a **development stack**.

```bash
docker compose up -d
```

Use it for local integration checks only. It is not the server deployment design
or evidence of production readiness. In particular, its local `grile:grile`
PostgreSQL credentials are rejected by prod startup validation and must not be
copied to a server configuration.

Inspect logs/status explicitly:

```bash
docker compose ps
docker compose logs --tail=200 api worker web
```

Stop with:

```bash
docker compose down
```

## 12. Clean local artefacts

```bash
make clean
```

Review the target before running it if a local environment contains anything
custom; it is intended for generated caches/dist and the ephemeral DB container.

## 13. Verification by task type

Use the evidence contract from `docs/QUALITY_GATES.md`:

- grid/rule changes → golden/edge tests;
- assignment/concurrency → real PostgreSQL;
- scope/auth → positive + forbidden cross-scope cases;
- calendar UI → component + browser proof;
- worker recovery → crash/stale/retry scenarios;
- XLSX → parse generated workbook;
- Google → fake structural suite; live canary only when explicitly approved;
- performance → realistic fixture + recorded timings/query counts;
- runtime config → startup-validation tests plus environment inventory;
- migrations/recovery → follow the backup/restore runbook rather than ad-hoc downgrade.

## 14. Retail reference rule

Local Grile development does **not** start, stop or modify UniHub Retail.

If a tracker task needs current Retail compatibility information, inspect the
Retail repository read-only and record only the contract/mapping needed in Grile.
Do not make Retail commits, migrations, config changes or service operations.

## 15. PR evidence template

A development PR should contain:

```text
Tracker IDs: SEC-..., FIN-..., FE-..., OPS-...
Contract/invariants changed:
Files/components:
Tests run:
PostgreSQL/runtime/browser evidence:
Not verified / limitations:
Follow-up tracker items:
```

After merge, update issue #4 immediately with the exact PR/commit evidence and
next READY task(s).
