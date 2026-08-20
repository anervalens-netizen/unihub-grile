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
The Alembic migration lands as `0001_initial_schema`; verify with
`docker exec ugrile-pg-s1 psql -U grile -d grile -c "\d site_day_assignments"`.

## 3. API + worker

```bash
make api    # foreground uvicorn on 127.0.0.1:8080
```

The FastAPI app exposes `/healthz`, `/readyz`, `/version`, the catalog
endpoints (`/catalog/{tenants,stores,people}`), the months/assignments
endpoints (`/months`, `/months/{id}/assignments`, `/months/{id}/coverage`),
the fixture ingest (`/ingest/fixture`, `/ingest/fixture/run`), and the
worker probe (`/worker/{jobs,run,noop}`).

The worker is in-process at S1; the same process exposes the typed jobs
through `/worker/run` so a later stage can move it to a dedicated container
without changing the API.

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

## 6. Targeted checks

```bash
make format       # ruff format
make lint         # ruff check
make typecheck    # mypy (backend) + tsc (frontend)
make test         # pytest (backend) + vitest (frontend)
make build        # vite production bundle
```

Each target is independent. Run `make test` before any CI push.

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
  -d '{"month_id":"month_tenantacme_2026-08","store_id":"store_alice","person_id":"person_alice","business_date":"2026-08-01","working_kind":"NORMAL"}'
```

(The second call exercises a single-agent store-day; a second similar call
in the same store-day yields 409 with `COVERAGE_INVARIANT`.)