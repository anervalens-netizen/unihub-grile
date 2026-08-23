# PostgreSQL backup, restore and migration runbook

Status: server-test candidate procedure  
Tracker: issue #4 (`OPS-005`)

This procedure is intentionally host-agnostic. It defines the safety sequence
required for the later server-test host; it does not deploy anything and it does
not authorize operations against UniHub Retail.

## Scope and data ownership

The authoritative standalone Grile state is PostgreSQL. It contains business
calendar/attendance projections, attribution/grid state, close/audit history,
durable jobs, Sheet bindings/reconciliation metadata and integration snapshots.

Generated XLSX artifacts under the managed `ugrile-s5-exports` root are
reproducible output, not the business database. Preserve them separately only
when an operator needs exact already-issued artifact evidence. Google Sheets are
external projections and are not a substitute for a PostgreSQL backup.

## Required tools and secret handling

Use PostgreSQL client tools compatible with the server major version (currently
PostgreSQL 17 in CI):

- `pg_dump`;
- `pg_restore`;
- `psql`;
- `sha256sum` (or an equivalent trusted SHA-256 tool).

Do not paste a password into a command line or runbook. Supply connection
parameters through the host's secret mechanism (`PGPASSFILE`, protected process
environment, secret manager, etc.). `DATABASE_URL` is a SQLAlchemy URL and may
contain `postgresql+psycopg://`; PostgreSQL CLI tools should use normal libpq
connection parameters/URI instead of blindly reusing that dialect-prefixed URL.

## Backup before migration or risky server-test work

Define a protected destination outside the application checkout. Example names:

```bash
export BACKUP_DIR=/var/backups/ugrile
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="$BACKUP_DIR/ugrile-$stamp.dump"
```

Create a custom-format logical backup:

```bash
umask 077
mkdir -p "$BACKUP_DIR"
pg_dump --format=custom --no-owner --no-acl --file="$backup"
sha256sum "$backup" > "$backup.sha256"
pg_restore --list "$backup" > "$backup.contents.txt"
```

The connection is supplied through libpq environment/secret configuration. A
backup is not accepted merely because `pg_dump` returned zero: require all of
these before a migration window continues:

1. dump file exists and is non-empty;
2. SHA-256 sidecar exists;
3. `pg_restore --list` can parse the archive;
4. backup/sidecar permissions are restricted;
5. operator records the source DB, UTC timestamp, application commit and current
   Alembic revision in the maintenance evidence.

## Restore drill — always to a separate database first

A restore test must not overwrite the active database. Provision an empty
**separate** database/user or isolated PostgreSQL instance, then restore there:

```bash
sha256sum --check "$backup.sha256"
pg_restore \
  --exit-on-error \
  --no-owner \
  --no-acl \
  --dbname="$RESTORE_TEST_DATABASE_URL" \
  "$backup"
```

`RESTORE_TEST_DATABASE_URL` must be a standard libpq PostgreSQL URI for the new
restore target, never the active primary URL.

Validate the restored copy:

```bash
DATABASE_URL="$RESTORED_SQLALCHEMY_URL" alembic current
DATABASE_URL="$RESTORED_SQLALCHEMY_URL" alembic check
```

Then run read-only sanity checks appropriate to the snapshot (table counts,
latest month/audit/job presence) and, when practical, the application
`/readyz` probe against an API instance pointed only at the restored test DB.
Do not run live Google mutations during a restore drill.

A backup is considered **restorable evidence** only after at least one isolated
restore drill succeeds; file existence alone is not enough.

## Forward migration sequence

For a later server-test update:

1. identify the exact candidate commit and migration head;
2. confirm mandatory CI is green on that exact commit;
3. inspect `alembic current`, `alembic heads` and migration files before changing
   the server-test DB;
4. quiesce Grile business writes and stop the durable worker (or perform the
   equivalent deployment drain) so no writer races the schema transition;
5. take and verify the pre-migration backup above;
6. run:

   ```bash
   alembic upgrade head
   ```

7. verify:

   ```bash
   alembic current
   alembic check
   ```

8. start the API, verify `/livez` then `/readyz`;
9. start/enable the worker, verify readiness again and inspect `/metrics`/Joburi
   for stale leases or failures;
10. execute the bounded server-test smoke/reconciliation plan for that candidate.

Do not start a new API binary against an older incompatible schema and rely on
runtime errors to reveal migration needs.

## Failed migration / rollback policy

Default policy is **run forward or restore**, not an improvised schema downgrade.

If `alembic upgrade head` fails before application traffic resumes:

1. keep API business writes and worker stopped/drained;
2. capture the migration error without exposing secrets/business payloads;
3. determine whether the failed transaction rolled back cleanly;
4. compare `alembic current` with the recorded pre-migration revision;
5. if the database cannot be proven consistent, restore the verified pre-change
   backup into a clean database/instance and repoint only after validation;
6. otherwise fix the migration in code, recertify it, and run forward.

Do **not** run `alembic downgrade` as a generic emergency command. A downgrade is
allowed only when the exact migration's downgrade path has been specifically
reviewed/tested for the affected data and the maintenance plan says to use it.
Destructive/downward data conversion is never inferred from the presence of a
`downgrade()` function.

## Restore-to-service sequence

Replacing an active Grile DB from backup is an exceptional operation and requires
an explicit maintenance window. The safe order is:

1. stop/drain API writes and worker;
2. preserve the failed/current DB rather than overwriting it in place;
3. restore the chosen verified archive to a **new clean** database/instance;
4. run `alembic current` and reconcile it with the application commit to be
   started;
5. apply only the reviewed forward migrations needed by that application;
6. run integrity/readiness checks against the restored target;
7. switch Grile connectivity to the validated restored database;
8. start API, then worker, and observe health/metrics/jobs;
9. retain the previous database and backup according to the host retention plan
   until server-test acceptance.

Never point this procedure at the UniHub Retail database. Retail remains outside
this standalone program and read-only even when Grile is being restored.

## Artifact directory

If exact generated export evidence must be preserved, snapshot the configured
`UGR_S5_EXPORT_DIR/ugrile-s5-exports` directory separately using host storage
mechanisms. Do not copy arbitrary `/tmp` trees. The normal XLSX retention cleaner
may remove old managed operation directories by age/count; PostgreSQL remains the
source from which deterministic artifacts can be regenerated.

## Evidence checklist

For a real server-test backup/migration window record, without secrets:

- exact Grile commit;
- pre/post Alembic revisions;
- backup filename, UTC timestamp and SHA-256;
- `pg_restore --list` success;
- isolated restore-drill result/reference;
- migration command result;
- post-migration `/livez` and `/readyz` state;
- worker/job/metric state after restart;
- any run-forward/restore decision and its rationale.
