# M3 PostgreSQL index / constraint review

Tracker: `BE-010`

This review is intentionally access-path driven. It does not add an index for
every foreign key or every filterable column. The target is the smallest set
that protects growing historical tables on production reads/worker loops while
preserving write cost and keeping the schema understandable.

## Evidence base

The review used the real M3 paths already exercised by CI:

- `worker.worker.claim_next` and `recover_stale_jobs`;
- `/worker/jobs/diagnostics`;
- `GET /months/{month_id}/grid` and grid replacement at one rule-pack revision;
- `repositories.epay.latest_snapshot`, called by payroll-grid calculation;
- `repositories.attribution.store_sales_for_month`;
- Google projection last-run / last-good readback;
- the M3 80-store / 160-person primary-screen and one-cell-save fixture.

## Indexes added

| Index | Production access path | Why the previous schema was insufficient |
| --- | --- | --- |
| `ix_outbox_pending_due (run_after, id) WHERE status='PENDING'` | Worker due-job claim | Scoped idempotency uniqueness is ordered by tenant/kind/key and cannot support due-time ordering. The partial index excludes terminal history. |
| `ix_outbox_running_lease (locked_at, id) WHERE status='RUNNING'` | Stale lease recovery | Recovery filters lease age only among RUNNING rows. Terminal queue history must not inflate the recovery scan. |
| `ix_outbox_tenant_status_id (tenant_id, status, id)` | Job diagnostics/history | Diagnostics scopes by tenant, partitions active/terminal status and reads newest IDs. The previous tenant-only index leaves status filtering to the heap. |
| `ix_grid_calculations_current_read (tenant_id, month_id, rule_pack_version, revision, store_id, person_id)` | Latest revision lookup + current grid rows + same-revision replacement | The correctness unique key places store/person before rule-pack/revision, so it is not a good prefix for the API's month/rule-pack/revision predicates. |
| `ix_epay_observations_latest_valid (...) WHERE is_valid IS true` | Latest valid E-pay snapshot for one person/store/month source | The existing `(tenant_id, observed_at)` index cannot narrow by store/person/source. Invalid forensic rows are deliberately excluded from this read index. |
| `ix_sales_store_day_tenant_date (tenant_id, business_date, store_id, generation)` | Month-range sales scan during attribution | The generation uniqueness key places store before date; a tenant-wide month date range cannot use that suffix selectively. |
| `ix_sheet_projection_runs_store_latest (tenant_id, store_id, id)` | Latest run / latest successful projection readback | Projection history previously had only the automatic tenant index, forcing store filtering across all tenant history. |

The index definitions live in `ugrile.repositories.index_contract` and are
attached to `Base.metadata`. Migration `0011_m3_query_path_indexes.py` is the
physical database change. CI now runs `alembic check` after a fresh PostgreSQL
bootstrap so migration/metadata drift fails the PR.

## Reviewed paths where no new index was added

- **Calendar assignments:** AC-02 partial unique indexes protect both working
  store-day and person-day, while `ix_site_day_assignments_month_store` matches
  the principal month/store/date read path.
- **Pontaj:** `ix_pontaj_projections_current` already starts with
  tenant/month/revision, matching current-revision reads. The remaining sort by
  person/date is bounded to one revision and does not justify another wide
  history index.
- **Attribution projection:** `ix_sales_person_day_projection_current` already
  starts with tenant/month/revision. Its correctness unique constraint is kept
  separately.
- **Manager scope / StoreAssignment / SalaryMaster:** current indexes begin with
  tenant + user/person + effective-from, matching effective-date resolution.
- **Holiday calendar/override:** the unique tenant/version/date keys protect
  writes and these tables remain tiny reference datasets. Repeated per-person
  holiday reads are a service batching concern, not a reason to create more
  low-value indexes.
- **AuditEvent:** current reads are entity-scoped and already use
  `ix_audit_tenant_entity`. Correlation ID remains diagnostic payload metadata;
  there is no production correlation-search API to justify a JSON/text index.

## Constraint review

No correctness constraint was weakened or replaced by a performance index.
The following database boundaries remain authoritative:

- composite tenant/store and tenant/person foreign keys;
- AC-02 partial unique working-assignment indexes;
- revision/generation uniqueness on Pontaj, attribution and grid snapshots;
- scoped `(tenant_id, kind, idempotency_key)` outbox uniqueness;
- effective-date validity checks and month/status enum checks;
- append-only close/audit behavior enforced by service transaction boundaries
  and existing persisted chain/projection contracts.

Potential exclusion constraints for overlapping effective-dated windows were
not added in this slice. The current product permits multiple simultaneous
manager-store scopes, and historical person/store ambiguity is intentionally
resolved/fails closed by the existing domain rules. A generic PostgreSQL range
exclusion would therefore encode business semantics that are not universally
valid and would reduce SQLite/test portability.

## Verification gate

`tests/integration/test_m3_postgres_index_contract.py` verifies on real
PostgreSQL that every new index materializes with its exact leading-column
order and that the queue/E-pay indexes retain their selective partial
predicates. The normal Backend CI additionally runs Ruff, strict mypy, fresh
`alembic upgrade head`, `alembic check`, M3 performance contracts and the full
regression suite.
