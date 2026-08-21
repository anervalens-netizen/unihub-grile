# Historical S1 architectural decisions

> **Historical context only.** These decisions describe the bootstrap stage and
> may mention obsolete stage sequencing or temporary mechanisms. Current scope,
> status and future direction are controlled by issues #3/#4 and the canonical
> docs. Durable technical decisions remain useful unless explicitly superseded.

| # | Decision | Rationale | Alternatives considered | When to revisit |
|---|---|---|---|---|
| D1 | Stack: Python 3.12, FastAPI 0.115, SQLAlchemy 2.0, Pydantic 2.9, Alembic 1.13, PostgreSQL 17, React 18 + Vite 5 + TypeScript 5 | Pinned by the bootstrap design; matches the current standalone implementation. FastAPI keeps the read/web layer explicit so Google I/O never reaches the request thread. | Django, Litestar, Lit/HonoJS frontends. | Only on a justified major stack change. |
| D2 | One durable worker is started via `python -m ugrile.worker.worker`; the API layer never executes jobs | The worker is the sole authority that settles typed jobs. The API can enqueue or list but cannot run a job inline. | Celery, RQ, Dramatiq, dedicated queue. | Revisit during runtime-resilience work if the simple outbox worker no longer meets the documented gate. |
| D3 | Assignment invariants enforced at pure domain + PostgreSQL partial unique indexes + real concurrent PostgreSQL test | Defence in depth: domain gives precise errors and DB remains the concurrency authority. | Application row locks; SERIALIZABLE. | If workload or model changes materially. |
| D3b | Composite-tenant integrity on foreign references plus tenant-safe synthetic IDs | Prevents cross-tenant references and connector collisions at DB/application boundaries. | Triggers; application-only checks. | Preserve unless replaced by an equally strong DB invariant. |
| D4 | Connector types are owned by Grile and structurally validated | Prevents the application from importing data shape directly from Retail/legacy source. | JSON Schema only; manual dataclasses. | Evolve into the versioned Retail integration contracts tracked by `INT-*`. |
| D5 | Bootstrap auth uses `X-Ugrile-Identity` + `X-Ugrile-Tenant` headers | It proved the authorization seam without choosing final host authentication. | OAuth/JWT/third-party auth. | **Already scheduled for hardening under `SEC-*`; this is not a production auth decision.** |
| D6 | Health/readiness probes use dedicated unauthenticated endpoints | Host probes/load balancers must not depend on user identity. | Authenticated probe. | Preserve; exact health semantics are hardened under `OPS-*`. |
| D7 | Physical sales authority stays in generation-keyed `SalesStoreDay` | Personal attribution must not overwrite or duplicate store/company physical totals. | Mutable person-owned sales table. | Preserve as core invariant. |
| D8 | Forward Alembic migration chain | Keeps schema changes explicit/reviewable. | Rewritten baseline; per-domain branches. | Reassess as migration history grows, without losing deterministic bootstrap. |
| D9 | Standalone frontend uses Vite + React + TypeScript and proxies `/api` to FastAPI | Appropriate for independent development. | SSR frameworks. | Final Retail integration may mount/port UI differently; current program does not require a premature rewrite. |
| D10 | SQLite can be used for fast unit tests, with PostgreSQL required for DB/concurrency claims | Fast feedback plus real-dialect proof where semantics matter. | PostgreSQL for every test. | Preserve evidence split unless implementation becomes PostgreSQL-specific enough to justify broader integration coverage. |
