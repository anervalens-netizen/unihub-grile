# S1 evidence — UniHub Grile foundation (attempt 2)

The handoff for Stage S1 of `UGR-001-STANDALONE-GRILE`. Every file in this
directory is a sanitised artefact captured on `2026-08-20` against the
exact candidate commit listed in the active ExecPlan's `Progress and
transitions` section.

| File | What it proves |
|---|---|
| `health-probe.txt` | `/healthz`, `/readyz`, `/version`, `/` return JSON with status `ok`, the live Alembic head (`b9fbb01f8cd0`) and the app version. No auth required. |
| `schema-evidence.txt` | `\dt` listing, `alembic_version`, the two partial unique indexes on `site_day_assignments` (`uq_site_day_one_working`, `uq_person_day_one_working`), the new composite foreign keys on `people`/`stores`/`site_day_assignments`/`sales_*`/`epay_observations`/`grid_calculations`/`sheet_*`/`store_assignments`, and the versioned `store_targets` table. |
| `ingest-fixture.txt` | `POST /ingest/fixture` lands 2 stores + 3 people + 3 sales + 2 targets under each tenant through the durable worker; `store_targets` carries `version=1`. |
| `ac02-conflict-probe.txt` | End-to-end conflict probes via the API: (1) Alice NORMAL on `store_acme_bucuresticenter` on `2026-08-01` succeeds (200); (2) Bob on the same store-day is rejected (409 `COVERAGE_INVARIANT` / `MULTIPLE_AGENTS_PER_STORE_DAY`); (3) Alice on `store_acme_clujnord` for the same day is rejected (409 `COVERAGE_INVARIANT` / `MULTIPLE_STORES_PER_AGENT_DAY`). The IDs are tenant-scoped. |
| `coverage-report.txt` | `GET /months/{id}/coverage` returns the empty conflict list after the first successful write, confirming the partial unique indexes are authoritative. |
| `worker-probe.txt` | `POST /worker/noop` enqueues a typed job; the durable worker settles it to `DONE`; `GET /worker/jobs?limit=10` lists it. `POST /worker/run` and `POST /ingest/fixture/run` are intentionally removed — the worker started via `python -m ugrile.worker.worker` is the sole authority. |
| `compose-stack-proof.txt` | Whole-stack proof through Docker Compose. The Vite dev server (UGRILE_API_PROXY=`http://api:8080`) proxies `/api/*` to the FastAPI container; `/api/healthz`, `/api/readyz`, `/api/ingest/fixture`, `/api/catalog/tenants`, and the AC-02 conflict probes all work end-to-end through the web. |
| `concurrent-ac02-pg.txt` | Real concurrent PostgreSQL AC-02 test. Two threads race to insert a `WORKING` row for the same `(store_id, business_date)` (and again for `(person_id, business_date)`); exactly one commits, the other gets an `IntegrityError` whose `pg diag.constraint_name` is the violated partial unique index. Also includes a two-tenant fixture isolation test that proves the composite `tenant_id`-aware FK rejects a cross-tenant `home_store_id`. |
| `test-summary.txt` | Backend `pytest`: `36 passed` (33 SQLite + 3 PostgreSQL integration); `ruff check`: clean; `mypy --strict`: 35 source files clean. |
| `typecheck-summary.txt` | Clean re-run of `ruff` and `mypy` against the candidate commit; no stale failures. |

## Reproducing

The full local stack runs under `docker compose up -d` with the rebuilt
`docker-compose.yml`. The web container now points its dev proxy at
`http://api:8080` (the compose-network hostname of the FastAPI
container); the `node_modules` directory lives in a named volume so the
read-only frontend bind mount does not leak root-owned files into the
developer's checkout.

```
docker compose up -d
docker exec ugrile-pg-local psql -U grile -d grile -c \
  "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO grile; GRANT ALL ON SCHEMA public TO public;"
docker exec ugrile-api-local /opt/venv/bin/alembic upgrade head
docker exec ugrile-api-local /opt/venv/bin/python -c \
  "import sys; sys.path.insert(0,'/app'); \
   from ugrile.core.database import reset_engine, get_sessionmaker; reset_engine(); \
   from ugrile.domain.enums import RoleName, MonthState; \
   from ugrile.domain.identifiers import make_tenant_id, make_month_id; \
   from ugrile.repositories.models import Tenant, User, Month; \
   S = get_sessionmaker(); \
   tid = make_tenant_id('acme'); s = S(); \
   s.add(Tenant(id=tid, name='Acme', timezone='Europe/Bucharest', is_active=True)); s.commit(); \
   s.add(User(id='user_admin', tenant_id=tid, email='admin@acme.example', display_name='Admin', role=RoleName.ADMIN.value, is_active=True)); s.commit(); \
   s.add(Month(id=make_month_id(tid, 2026, 8), tenant_id=tid, year=2026, month=8, state=MonthState.OPEN, revision=0)); s.commit()"

# API direct
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/readyz

# Through the web proxy
curl -fsS http://127.0.0.1:5173/api/healthz
curl -fsS -X POST http://127.0.0.1:5173/api/ingest/fixture \
  -H 'X-Ugrile-Identity: user_admin' -H 'X-Ugrile-Tenant: tenant_acme' \
  -H 'Content-Type: application/json' -d '{"tenant_token":"acme"}'

# Real concurrent PG AC-02
UGRILE_PG_URL=postgresql+psycopg://grile:grile@127.0.0.1:55432/grile \
  .venv/bin/python -m pytest tests/integration/test_postgres_concurrent_ac02.py -v
```

All evidence above was captured against the candidate commit on
`2026-08-20`. The full play-by-play lives in `docs/operations/local-commands.md`.
