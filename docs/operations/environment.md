# Runtime environment reference

Status: server-test candidate contract  
Tracker: issue #4 (`OPS-004`, `OPS-007`)

This file is the canonical inventory of environment variables consumed by the
standalone Grile runtime, local stack, frontend dev proxy and CI helpers. It does
not contain credentials or authorize a deployment.

## Backend application settings

`backend/src/ugrile/core/config.py` owns the settings below. Names without an
explicit `UGRILE_*` alias follow Pydantic Settings' field-name convention.

| Variable | Default | Scope | Contract |
| --- | --- | --- | --- |
| `APP_NAME` | `ugrile-backend` | API/worker | Diagnostic application name. |
| `APP_ENV` | `dev` | API/worker | `dev`, `test`, `ci`, `prod`. `prod` activates startup safety validation. |
| `LOG_LEVEL` | `INFO` | API/worker | Python/structlog threshold. PII-safe field filtering remains active at every level. |
| `TIMEZONE` | `Europe/Bucharest` | backend | Default business timezone setting. Tenant/business-date rules remain authoritative where explicitly stored. |
| `DATABASE_URL` | local `grile:grile` PostgreSQL URL | API/worker/migrations | SQLAlchemy URL. Prod requires PostgreSQL and rejects the development credential pair. Secret in real environments. |
| `DATABASE_ECHO` | `false` | backend | SQLAlchemy SQL echo. Must be `false` in prod because SQL/parameters are not an approved observability channel. |
| `IDENTITY_PROVIDER` | `dev_headers` | API | `dev_headers` or reserved `external`. `dev_headers` is local/test only. `external` is also rejected in `prod` until a real adapter exists, preventing false production readiness. |
| `WORKER_ENABLED` | `true` | API/worker | Declares durable worker responsibility. Readiness checks stale RUNNING leases only when enabled. |
| `UGRILE_WORKER_POLL_SECONDS` | `0.5` | worker | Durable outbox poll interval. |
| `UGRILE_WORKER_LEASE_SECONDS` | `1800` | API/worker | RUNNING lease timeout; minimum 30 seconds. Used by recovery/readiness. |
| `CONNECTOR_DEFAULT` | `fixture-v1` | backend | Development fixture selector. Fixture ingest route is not mounted in prod. Future Retail adapter must remain behind the connector contract. |
| `UGRILE_GOOGLE_PROVIDER` | `fake` | API/worker | `fake` or `live`. `fake` is rejected in prod because it can otherwise look operational while performing no provider I/O. |
| `UGRILE_GOOGLE_CREDENTIALS_FILE` | unset | API/worker | Absolute path to externally mounted credential file. Never credential JSON itself. Required for live mutations and prod live provider. |
| `UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED` | `false` | worker/API gates | Explicit second gate for Google writes. Requires provider `live` + credential path. |
| `UGRILE_EXPORT_RETENTION_HOURS` | `168` | cleanup | Maximum age of managed export operation artifacts; minimum 1. |
| `UGRILE_EXPORT_MAX_OPERATIONS` | `500` | cleanup | Maximum managed export operation entries retained; minimum 1. |

## Backend direct runtime variables

These variables are intentionally outside the Pydantic Settings model because
they configure process/container edges or legacy-compatible artifact paths.

| Variable | Default | Scope | Contract |
| --- | --- | --- | --- |
| `UGRILE_HOST` | `127.0.0.1` | `python -m ugrile.main` | Uvicorn bind host for the programmatic entrypoint. Reverse proxy/firewall ownership is deployment-specific. |
| `UGRILE_PORT` | `8080` | `python -m ugrile.main` | Uvicorn bind port. |
| `UGR_S5_EXPORT_DIR` | OS temporary directory when not set | export worker | Parent for the managed `ugrile-s5-exports` artifact root. Server test should mount a bounded persistent/writable path rather than rely on `/tmp`. |
| `UGR_S5_GOOGLE_FAIL` | unset | fake Google provider only | Fault injection for deterministic local/test retry checks. Must never be enabled as an operational setting. |

## Frontend development variable

| Variable | Default | Scope | Contract |
| --- | --- | --- | --- |
| `UGRILE_API_PROXY` | local Vite default | Vite dev server | Destination for `/api` development proxy. The Compose stack uses `http://api:8080`. It is not a backend/server deployment setting. |

The production frontend bundle does not receive identity, payroll, Google or DB
credentials through Vite environment variables.

## Test/CI helper

| Variable | Scope | Contract |
| --- | --- | --- |
| `UGRILE_PG_URL` | PostgreSQL integration/performance tests | Explicit test database URL used only by test harnesses. GitHub Actions points it at the disposable PostgreSQL service. |

GitHub Actions also sets `APP_ENV=ci` and `DATABASE_URL` for backend jobs. Those
are ordinary application settings with CI-specific values, not separate config
contracts.

## Production startup validation

Constructing `Settings` is part of API/worker startup. At the current standalone
milestone, `APP_ENV=prod` is intentionally **not startable** because no production
identity adapter exists yet. This prevents a process with unusable authentication
from advertising healthy production readiness.

`APP_ENV=prod` fails before serving traffic when any of these conditions is true:

- `IDENTITY_PROVIDER=dev_headers`;
- `IDENTITY_PROVIDER=external` while the external adapter remains unimplemented;
- `DATABASE_URL` is not PostgreSQL;
- `DATABASE_URL` uses the repository's development `grile:grile` credential pair;
- `DATABASE_ECHO=true`;
- `UGRILE_GOOGLE_PROVIDER=fake`;
- prod live provider has no credential path or uses a relative credential path.

Independent of environment, enabling Google live mutations also requires
`UGRILE_GOOGLE_PROVIDER=live` and an absolute
`UGRILE_GOOGLE_CREDENTIALS_FILE` path.

Production can only become startable in a later integration milestone that adds
a real identity adapter and updates this validator with executable tests proving
that adapter is available. Merely selecting `IDENTITY_PROVIDER=external` is not
sufficient.

## Secrets and forbidden values

Treat these as secret-bearing:

- real `DATABASE_URL` credentials;
- the file referenced by `UGRILE_GOOGLE_CREDENTIALS_FILE`;
- any future external identity/session signing material.

Do not put service-account JSON, passwords, tokens, cookies, employee data or
payroll values in `.env.example`, GitHub Actions logs, application logs or
command history. Prefer the host secret mechanism and file mounts.

## Server-test preflight

Before starting API/worker on a server-test host:

1. create the environment from this inventory rather than copying local Compose defaults;
2. use `APP_ENV=test` until production identity integration is actually implemented and separately verified;
3. use a dedicated PostgreSQL database/user with non-default credentials;
4. run migrations before starting API/worker;
5. use a persistent bounded export path if artifact download testing is required;
6. keep Google fake unless a separately authorized bounded canary window is being executed;
7. verify `/livez`, `/readyz` and `/metrics` after startup;
8. never start or mutate `unihub-retail` as part of this standalone program.
