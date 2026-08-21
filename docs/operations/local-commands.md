# Local commands — S1

The standalone foundation runs on a single workstation without depending on
Retail or Google. Every command below is reproducible on the developer host
(`dell-standby`) and on any CI runner with Docker + Python 3.12 + Node 20+.

## 1. One-time bootstrap

```bash
make install
```

This creates `backend/.venv`, installs the pinned Python dependencies
(`fastapi==0.115.5`, `sqlalchemy==2.0.36`, `psycopg[binary]==3.2.3`,
`alembic==1.13.3`, `pydantic==2.9.2`, etc.) and runs `pnpm install` for the
frontend.

## 2. Ephemeral PostgreSQL

```bash
make pg-up        # start a dedicated postgres:17-alpine on 127.0.0.1:55432
make migrate      # alembic upgrade head
make pg-down      # stop the container
```

The container is named `ugrile-pg-s1` and uses `grile`/`grile` credentials.
The Alembic chain lands as `0001_initial_schema` then
`0002_composite_tenant_fks_and_targets`; verify with
`docker exec ugrile-pg-s1 psql -U grile -d grile -c "\d site_day_assignments"`.

## 3. API + worker

```bash
make api    # foreground uvicorn on 127.0.0.1:8080
```

The FastAPI app exposes `/healthz`, `/readyz`, `/version`, the catalog
endpoints (`/catalog/{tenants,stores,people}`), the months/assignments
endpoints (`/months`, `/months/{id}/assignments`, `/months/{id}/coverage`),
the fixture ingest (`/ingest/fixture` — enqueue only), and the worker
read-only endpoints (`/worker/jobs`, `/worker/noop`).

The durable worker runs as a separate process via
`python -m ugrile.worker.worker` and is the **only** authority that
executes jobs. The previous attempt's `POST /worker/run` and
`POST /ingest/fixture/run` were removed because they violated the single
authority contract.

## 4. Health probe

```bash
make health
```

Probes `/healthz` (liveness + DB ping) and `/readyz` (Alembic version + DB
probe). Both return JSON with the schema version and the app version.

## 5. Frontend dev server

```bash
make web    # vite dev on 127.0.0.1:5173, proxying /api -> 127.0.0.1:8080
```

The dev shell renders the foundation pages (`Health`, `Overview`) and uses
the `X-Ugrile-Identity` / `X-Ugrile-Tenant` headers. Real authentication is
out of scope for S1.

When run via Docker Compose, the web container sets
`UGRILE_API_PROXY=http://api:8080` so the proxy reaches the FastAPI
container regardless of host port mappings. `node_modules` lives in a
named volume (`ugrile-web-node_modules`) so the bind mount does not leak
root-owned files into the developer's checkout.

## 6. Targeted checks

```bash
make format       # ruff format
make lint         # ruff check
make typecheck    # mypy (backend) + tsc (frontend)
make test         # pytest (backend) + vitest (frontend)
make build        # vite production bundle
```

Each target is independent. Run `make test` before any CI push. The
backend pytest suite includes both SQLite unit tests and a real PostgreSQL
integration test; the latter runs only when `UGRILE_PG_URL` points at a
reachable PG server.

## 7. Smoke (api + migration in one shot)

```bash
make smoke
```

Brings up Postgres, applies migrations, boots the API in the background,
probes `/healthz`, `/readyz`, `/version`, then kills the API. Use this to
prove the local stack end-to-end.

## 8. AC-02 conflict probe

```bash
make migrate
make api &        # background
sleep 1
curl -fsS -X POST http://127.0.0.1:8080/ingest/fixture \
  -H "X-Ugrile-Identity: user_admin" \
  -H "X-Ugrile-Tenant: tenant_acme" \
  -H "Content-Type: application/json" \
  -d '{"tenant_token":"acme"}' | python3 -m json.tool

# Expect HTTP 409 with code = COVERAGE_INVARIANT
curl -i -X POST http://127.0.0.1:8080/months/month_tenantacme_2026-08/assignments \
  -H "X-Ugrile-Identity: user_admin" \
  -H "X-Ugrile-Tenant: tenant_acme" \
  -H "Content-Type: application/json" \
  -d '{"month_id":"month_tenantacme_2026-08","store_id":"store_acme_bucuresticenter","person_id":"person_acme_alice","business_date":"2026-08-01","working_kind":"NORMAL"}'
```

(The first call enqueues a fixture job; the worker settles it. The second
call exercises a single-agent store-day; a second similar call in the
same store-day yields 409 with `COVERAGE_INVARIANT`.)

## 9. Real concurrent PostgreSQL AC-02

```bash
UGRILE_PG_URL=postgresql+psycopg://grile:grile@127.0.0.1:55432/grile_test \
  .venv/bin/python -m pytest tests/integration/test_postgres_concurrent_ac02.py -v
```

Three integration tests cover: (a) two threads racing the
`uq_site_day_one_working` partial unique index, (b) two threads racing
`uq_person_day_one_working`, and (c) a two-tenant fixture isolation probe
that proves the composite `(tenant_id, home_store_id)` FK rejects a
cross-tenant `home_store_id`. The tests are skipped automatically when
`UGRILE_PG_URL` is unreachable.

## 10. Whole-stack via Docker Compose

```bash
docker compose up -d
# `migrate` completes before API/worker start; seed admin/month
docker exec ugrile-api-local python -c \
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
# probe through the web proxy
curl -fsS http://127.0.0.1:5173/api/healthz
curl -fsS -X POST http://127.0.0.1:5173/api/ingest/fixture \
  -H "X-Ugrile-Identity: user_admin" -H "X-Ugrile-Tenant: tenant_acme" \
  -H "Content-Type: application/json" -d '{"tenant_token":"acme"}'
```

The Vite dev server inside the `web` container proxies `/api/*` to the
FastAPI container at `http://api:8080` (compose service name), so the
read-UI path is provable end-to-end. The durable worker container runs
`python -m ugrile.worker.worker` and drains the outbox asynchronously.
