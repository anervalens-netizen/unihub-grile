# S1 evidence — UniHub Grile foundation

The handoff for Stage S1 of `UGR-001-STANDALONE-GRILE`. Every file in this
directory is a sanitised artefact captured on `2026-08-20` against the
exact commit listed in the active ExecPlan's `Next exact step` section.

| File | What it proves |
|---|---|
| `health-probe.txt` | `/healthz`, `/readyz`, `/version`, `/` return JSON with status `ok` and the live schema/app version. No auth required. |
| `schema-evidence.txt` | `\dt` listing, `alembic_version`, and the two partial unique indexes on `site_day_assignments` (`uq_site_day_one_working`, `uq_person_day_one_working`) — the AC-02 enforcement at the DB transaction boundary. |
| `ingest-fixture.txt` | `POST /ingest/fixture` lands 2 stores, 3 people, 3 sales rows under `tenant_acme` using the v1 fixture payload. |
| `ac02-conflict-probe.txt` | End-to-end conflict probes via the API: (1) Alice assigned to `store_bucuresticenter` on `2026-08-01` succeeds (200); (2) Bob on the same store-day is rejected (409 `COVERAGE_INVARIANT` / `MULTIPLE_AGENTS_PER_STORE_DAY`); (3) Alice on `store_clujnord` for the same day is rejected (409 `COVERAGE_INVARIANT` / `MULTIPLE_STORES_PER_AGENT_DAY`). |
| `coverage-report.txt` | `GET /months/{id}/coverage` returns the empty conflict list after the first successful write, confirming the repository round-trip is consistent. |
| `worker-probe.txt` | `POST /worker/noop` enqueues a typed job; `POST /worker/run` settles it to `DONE`; `GET /worker/jobs?limit=5` lists it. The single durable worker is wired. |
| `test-summary.txt` | Backend: `pytest` (28 passed), `ruff check` (clean), `mypy` (clean, 35 source files). Frontend: `vitest` (2 passed), `tsc --noEmit` (clean). |

## Reproducing

```
# Ephemeral Postgres + migrations
docker run -d --rm --name ugrile-pg-s1 -p 55432:5432 \
  -e POSTGRES_DB=grile -e POSTGRES_USER=grile -e POSTGRES_PASSWORD=grile \
  postgres:17-alpine
cd backend && DATABASE_URL=postgresql+psycopg://grile:grile@127.0.0.1:55432/grile \
  .venv/bin/alembic upgrade head

# Seed tenant/admin/month so the API has an admin principal to authenticate
DATABASE_URL=postgresql+psycopg://grile:grile@127.0.0.1:55432/grile .venv/bin/python \
  -c "from ugrile.core.database import reset_engine, get_sessionmaker; reset_engine(); \
      from ugrile.domain.enums import RoleName, MonthState; \
      from ugrile.domain.identifiers import make_tenant_id, make_month_id; \
      from ugrile.repositories.models import Tenant, User, Month; \
      S = get_sessionmaker(); \
      tid = make_tenant_id('acme'); \
      s = S(); s.add(Tenant(id=tid, name='Acme', timezone='Europe/Bucharest', is_active=True)); s.commit(); \
      s.add(User(id='user_admin', tenant_id=tid, email='admin@acme.example', display_name='Admin', role=RoleName.ADMIN.value, is_active=True)); s.commit(); \
      s.add(Month(id=make_month_id('tenant_acme', 2026, 8), tenant_id=tid, year=2026, month=8, state=MonthState.OPEN, revision=0)); s.commit()"

# API
DATABASE_URL=postgresql+psycopg://grile:grile@127.0.0.1:55432/grile \
  .venv/bin/uvicorn ugrile.main:app --host 127.0.0.1 --port 8080

# Probes
curl -fsS http://127.0.0.1:8080/healthz | python3 -m json.tool
curl -fsS -X POST http://127.0.0.1:8080/ingest/fixture \
  -H "X-Ugrile-Identity: user_admin" -H "X-Ugrile-Tenant: tenant_acme" \
  -H "Content-Type: application/json" -d '{"tenant_token":"acme"}' | python3 -m json.tool
# etc.
```

The full play-by-play lives in `docs/operations/local-commands.md`.