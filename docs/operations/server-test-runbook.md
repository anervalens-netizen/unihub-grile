# Standalone server-test runbook

Status: `VAL-014` candidate procedure.  
Canonical plan: issue #3.  
Canonical status/evidence ledger: issue #4.

This runbook is for testing UniHub Grile as a standalone candidate on a
non-production server. It does **not** authorize production deployment, live
Google mutation, modification of UniHub Retail, or real Retail integration.

## 1. Candidate identity

The exact candidate SHA is recorded in issue #4 after final exact-head CI and
SHA-guard merge. This file intentionally uses the placeholder
`<CANDIDATE_SHA>` rather than attempting to name its own commit.

Server test must start from an immutable commit, not `main` or another moving
branch:

```bash
git fetch origin --prune
git checkout --detach <CANDIDATE_SHA>
git rev-parse HEAD
git status --short
```

Acceptance:

- `git rev-parse HEAD` exactly equals the candidate SHA recorded in issue #4;
- worktree is clean;
- no local patch, generated source, dependency override or untracked runtime code
  is allowed to influence the tested artifact;
- record the SHA in the private server-test evidence before starting processes.

`GET /version` reports application version/environment but is **not** a Git SHA
attestation. Artifact identity is established from the exact checkout/build.

## 2. Safety boundary

Before any server work, confirm:

- target is a standalone Grile test host/environment;
- UniHub Retail will not be started, stopped, modified, migrated or written by
  this procedure;
- database is dedicated to Grile server test;
- no production employee/customer/payroll dataset is required;
- Google provider remains `fake` unless a separate live-canary authorization is
  recorded;
- no production DNS/routing promotion is part of this runbook.

Stop if satisfying a step appears to require a Retail code/config/database
change. That belongs to a later explicitly authorized integration milestone.

## 3. Required software

Use versions compatible with the certified repository:

- Python 3.12;
- PostgreSQL 17-compatible server/client tooling;
- Node 20+ and pnpm for the standalone frontend when built on-host;
- Git;
- a host service supervisor for API/worker processes;
- reverse proxy/TLS only if needed by the isolated test environment.

Container/local Compose defaults are development helpers, not server
configuration standards.

## 4. Environment

Use `docs/operations/environment.md` as the complete variable inventory.

For standalone server test, the safe default profile is conceptually:

```text
APP_ENV=test
IDENTITY_PROVIDER=dev_headers
DATABASE_URL=<dedicated non-default PostgreSQL credentials>
DATABASE_ECHO=false
WORKER_ENABLED=true
UGRILE_GOOGLE_PROVIDER=fake
UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED=false
UGR_S5_EXPORT_DIR=<bounded writable server-test path>
```

Important rules:

- bind the server test to a restricted network; `dev_headers` is not production
  authentication;
- use non-default PostgreSQL credentials;
- never copy Google credential JSON into the repo or inline it into evidence;
- `APP_ENV=prod` is intentionally rejected until the real external identity
  adapter exists;
- `UGRILE_GOOGLE_PROVIDER=live` by itself does not authorize a write;
- live Google writes require the second mutation gate and separate canary
  authorization.

Record non-secret environment values and secret-source references, not secret
contents.

## 5. Install and build

From the exact detached candidate checkout:

```bash
make install
make lint
make typecheck
make test
make build
```

A server-test deployment may use prebuilt artifacts instead, but their provenance
must resolve to the same `<CANDIDATE_SHA>`.

Do not continue with failed verification or an unexplained dependency drift.

## 6. Database creation and migration

Provision a dedicated empty PostgreSQL database/user, then point `DATABASE_URL`
at it.

Before first application start:

```bash
cd backend
alembic heads
alembic upgrade head
alembic current
alembic check
```

Acceptance:

- one expected migration head;
- `upgrade head` succeeds;
- `alembic current` equals shipped head;
- `alembic check` reports no new upgrade operations.

For an **existing valuable server-test database**, follow
`docs/operations/backup-restore-migrations.md` before migrations. That procedure
requires verified backup evidence and restore-first/run-forward behavior rather
than an improvised downgrade.

## 7. Start order

Recommended order:

1. PostgreSQL;
2. migrations complete;
3. API;
4. `/livez` and `/readyz` check;
5. worker;
6. readiness/metrics re-check;
7. frontend/reverse proxy.

Example API process from repository checkout:

```bash
cd backend
python -m ugrile.main
```

Example worker in a separately supervised process:

```bash
cd backend
python -m ugrile.worker.worker
```

The host supervisor owns process liveness/restart policy. Grile `/readyz` checks
DB/schema and stale RUNNING leases; it does not claim to be a worker heartbeat.

## 8. Immediate runtime probes

After API start:

```bash
curl -fsS http://127.0.0.1:8080/livez
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/readyz
curl -fsS http://127.0.0.1:8080/version
curl -fsS http://127.0.0.1:8080/metrics
```

Require:

- `/livez`: HTTP 200;
- `/readyz`: HTTP 200, DB current, schema current, no stale worker lease;
- `/version`: expected standalone candidate application version/environment;
- `/metrics`: parseable Prometheus text without tenant/store/person/payroll
  identifiers.

Record the externally attested candidate SHA alongside these probes.

## 9. Synthetic fixture setup

Use only deterministic synthetic/anonymized server-test data.

When fixture ingestion is required, it is available because `APP_ENV=test` is
non-production. Use an authorized test admin principal and the documented
fixture route. Run the worker until the ingest job reaches a terminal successful
state before judging downstream screens.

Do not import real Retail tables, copy production DB rows, or treat blank source
fields as financial zero.

## 10. Core application smoke

Use at least one ADMIN and one MANAGER synthetic principal.

Validate:

### Session and capabilities

- `GET /session` returns the expected tenant, role and capabilities;
- mismatched identity/tenant headers are rejected;
- MANAGER cannot acquire ADMIN-only close/config capabilities.

### Overview / Program

- Overview loads for representative fixture scale;
- Program loads current revision;
- one valid cell change succeeds;
- stale revision produces the typed conflict without losing the pending user
  context;
- out-of-scope person/store mutation is rejected;
- denial creates no assignment/audit/job side effect.

### Store / Pontaj / attribution / grid

- store detail loads real API data;
- Pontaj reflects the Program state;
- personal attribution changes only personal credit, not physical store totals;
- grid calculation is reproducible for current revision/generation;
- missing required financial inputs remain explicit anomaly/blocker rather than
  zero.

### Close / reopen on synthetic month

Use a disposable synthetic month only:

- checklist exposes blockers before close;
- close refuses while required blockers exist;
- after valid synthetic inputs, one close succeeds;
- CLOSED business mutations are rejected;
- reopen requires authorized admin + non-empty reason;
- previous close event remains in the audit chain.

Do not use a real payroll period.

## 11. Worker/recovery smoke

Inspect the Joburi/API diagnostics for queued/running/done/failed state.

At minimum verify:

- normal export/Google-fake job reaches DONE;
- a known deterministic test failure is terminal rather than retried forever;
- a retryable synthetic fault follows bounded backoff;
- no stale RUNNING job remains after recovery exercise;
- correlation ID can be followed from API request into worker diagnostics/logs.

Use `docs/operations/worker-recovery.md` for remediation. Never hand-edit outbox
status as a normal recovery mechanism.

## 12. XLSX smoke

Generate a representative per-store export and, if needed, bulk/pontaj-only
artifacts through the normal API/worker path.

Validate:

- file can be opened by a standard XLSX parser/application;
- required `Grila` and `Pontaj` tabs are present for per-store export;
- bulk manifest/checksum matches payload;
- no external Retail/Google links/formula dependencies appear;
- repeated same canonical input is deterministic according to the existing
  serializer contract;
- managed export storage remains within the configured retention root.

Do not move generated artifacts into Retail storage.

## 13. Performance measurement

Use the same representative scale as the CI performance contract: equivalent to
at least 80 stores / 160 people for August-like coverage.

Record:

- host CPU/RAM/storage class;
- PostgreSQL placement/version;
- Overview latency and query count;
- Program load latency and query count;
- Grid latency and query count;
- composite Store-screen latency/query count;
- one-cell Program save wall time and SQL statement counts.

The structural regression budgets remain authoritative. The one-cell save path
must stay near the certified **20 SELECT / 29 total statements** contract; a
return to large statement fan-out is a failure even if the host is fast.

The local/pilot objectives from `docs/QUALITY_GATES.md` remain useful targets,
but host variance must be recorded rather than hidden. A severe expected-scale
latency issue is a P1 and blocks wider test progression until understood.

## 14. Browser validation

Run the real frontend against the server-test API/PostgreSQL stack and validate
at least:

- overview/navigation;
- Program read/edit/conflict recovery;
- store detail;
- exceptions;
- Management close/reopen on synthetic data;
- Joburi/export state;
- permission-denied control visibility;
- one subsystem failure without whole-page collapse;
- desktop/tablet/mobile representative viewport behavior;
- keyboard operation of primary forms/tabs/calendar controls.

Capture no real employee/customer/payroll data in screenshots or browser
artifacts.

## 15. Google live canary — separate authorization only

Default server-test behavior is `UGRILE_GOOGLE_PROVIDER=fake` with live mutations
disabled.

Do **not** execute live Google operations from this runbook unless the user has
separately authorized the bounded canary and named/approved a non-production
target.

When authorized, follow **only**
`docs/operations/google-live-canary.md`. Its hard bounds include one test tenant,
one OPEN test month, one store, one disposable spreadsheet, at most two
projections and at most one controlled E-pay readback.

Stop on the first provider/protection/readback mismatch. A failing canary is not
converted to PASS by blind repetition.

## 16. Backup / restore drill

Before server-test state becomes a dependency for others or before a risky
migration window, execute the backup procedure in
`docs/operations/backup-restore-migrations.md`.

A backup is accepted as restorable evidence only after an isolated restore test
succeeds. Never restore over the active database merely to prove the dump works.

## 17. Stop / rollback

Stopping standalone server test must be reversible without Retail changes.

Safe rollback boundary:

1. stop/drain Grile frontend/API writes;
2. stop worker;
3. preserve current Grile DB/log/evidence state;
4. remove/disable Grile routing only;
5. for schema/data recovery, run forward or restore a verified Grile backup to a
   clean database according to the migration runbook;
6. keep Google live mutations disabled;
7. do not perform any Retail rollback because this program did not change
   Retail.

Do not use destructive `alembic downgrade`, manual outbox deletion or direct
business-table edits as generic recovery shortcuts.

## 18. Evidence record

For each server-test session record, without secrets/PII:

```text
result: PASS | FAIL | PARTIAL
candidate_sha: <exact issue-4 candidate SHA>
checkout_clean: true|false
host_profile: <non-secret CPU/RAM/storage/OS summary>
postgres_version: <version>
alembic_head: <revision>
api_livez: PASS|FAIL
api_readyz: PASS|FAIL
metrics_probe: PASS|FAIL
worker_supervisor: PASS|FAIL
fixture_scope: <synthetic cohort description>
auth_scope_smoke: PASS|FAIL
program_smoke: PASS|FAIL
pontaj_attribution_grid: PASS|FAIL
close_reopen_synthetic: PASS|FAIL
worker_recovery_smoke: PASS|FAIL
xlsx_smoke: PASS|FAIL
browser_smoke: PASS|FAIL
performance_summary: <latencies/query counts>
backup_restore_drill: PASS|FAIL|NOT_RUN
google_live_canary: PASS|FAIL|NOT_AUTHORIZED
retail_mutations: none
open_findings: <IDs/descriptions>
```

If live Google canary is separately run, store its own sanitized evidence using
the template in the canary document.

## 19. Server-test session PASS criteria

A standalone server-test session is PASS only when:

- the exact candidate SHA was tested from a clean artifact;
- migrations/readiness are current;
- primary synthetic business flows succeed;
- no authorization, data-loss or closed-month bypass appears;
- worker jobs are recoverable/diagnosable;
- browser and XLSX smoke succeed;
- observed performance has no unexplained severe regression;
- no new P0/P1 correctness/scope/data-loss/financial-close defect is open;
- Retail remained untouched;
- any live Google activity stayed within a separately authorized bounded canary.

A server-test PASS still does not authorize production or Retail integration.