# Google Sheet binding lifecycle

Status: operational contract for M5 `GS-003`.

This runbook defines how one UniHub Grile store is bound to one Google
spreadsheet and how a binding may be changed safely. It does not authorize a
live Google canary or deployment; those remain separate server-test actions.

## Invariants

1. A store has at most one `sheet_bindings` row.
2. A Google `spreadsheet_id` may be bound to only one store globally. The
   database unique constraint is the final authority, including concurrent
   requests.
3. Binding configuration is an administrative action protected by
   `sheet.bind`. Projection/sync permission does not imply binding permission.
4. Projection output is not binding configuration. A projection may advance
   the binding generation after successful publication, but it must never
   create, re-point or rename a configured binding as a side effect.
5. An identity is the complete tuple:
   `spreadsheet_id + sheet_name_grila + sheet_name_pontaj`.
6. Any identity change is an explicit rebind. Rebind requires:
   - the complete last-seen identity as compare-and-swap input;
   - a non-trivial operator reason;
   - a transactional audit event containing before/after identity and reason.
7. Exact replay of an already-applied create/rebind is a no-op. This makes a
   retry after an HTTP response is lost safe and does not create duplicate
   audit events.
8. Every API-enqueued Google projection job pins the complete binding identity
   observed at enqueue. A later rebind or tab rename must make the older job
   fail terminally before provider I/O; it may not be redirected to the new
   workbook or tabs.
9. Legacy or hand-crafted projection jobs without binding pin metadata fail
   closed.
10. In fake mode, the deterministic fake spreadsheet identity is allowed only
    when no explicit binding exists. Fake projection must not overwrite a
    manually configured binding.

## API

Read the current binding:

```http
GET /sheet-bindings/{store_id}
```

Create the initial binding:

```json
{
  "spreadsheet_id": "google-spreadsheet-id",
  "sheet_name_grila": "Grila",
  "sheet_name_pontaj": "Pontaj"
}
```

Rebind to another spreadsheet or rename either tab:

```json
{
  "spreadsheet_id": "new-google-spreadsheet-id",
  "sheet_name_grila": "Grila",
  "sheet_name_pontaj": "Pontaj",
  "expected_current_spreadsheet_id": "old-google-spreadsheet-id",
  "expected_current_sheet_name_grila": "Grila",
  "expected_current_sheet_name_pontaj": "Pontaj",
  "reason": "replace retired workbook"
}
```

The last-seen values must come from the binding previously read by the
operator/client. Do not synthesize them from configuration files.

## Expected conflicts

- `SHEET_BINDING_STALE`: the binding no longer matches the complete last-seen
  identity, or CAS values were supplied for a binding that does not exist.
- `SHEET_SPREADSHEET_ALREADY_BOUND`: another store already owns the requested
  spreadsheet.
- `SHEET_REBIND_REASON_REQUIRED`: an identity change was requested without an
  adequate reason.
- `SHEET_BINDING_TABS_COLLIDE`: Grila and Pontaj resolve to the same tab name.
- `GOOGLE_SHEET_BINDING_STALE`: a durable projection job was pinned to an
  identity that has since changed. The worker treats this as terminal, not
  retryable.
- `JOB_METADATA_MISSING`: an old/hand-crafted projection job lacks required
  binding pin metadata and is rejected before publication.

## Rebind procedure

1. `GET /sheet-bindings/{store_id}` and retain the exact three identity fields.
2. Confirm the target spreadsheet is the intended permanent workbook for this
   store and that the Grila/Pontaj tab names are correct.
3. `PUT /sheet-bindings/{store_id}` with the target identity, complete
   last-seen identity and reason.
4. If the request times out after submission, retry the exact same request.
   Exact replay is intentionally idempotent.
5. If `SHEET_BINDING_STALE` is returned, read the binding again and reassess;
   do not blindly overwrite the newer identity.
6. Enqueue a new projection after the rebind. Do not revive an older failed
   job pinned to the previous identity.

## Failure and recovery semantics

A rebind resets binding generation to `UNPROJECTED`. The next successful
projection establishes the new generation. Last-good projection history is not
rewritten by the binding operation itself.

A projection job queued before a rebind keeps its original identity pin. At
execution the provider compares that pin with the current binding before any
live transport read/write. A mismatch is a terminal business failure. The
operator should enqueue a new projection for the current binding rather than
retrying the stale job.

The default projection idempotency identity includes the binding fingerprint as
well as month/data revision. Therefore a rebind creates a new logical
projection operation even when the month revision did not change.

## Database migration

Migration `0012_sheet_binding_lifecycle.py` adds the global unique constraint on
`sheet_bindings.spreadsheet_id`. Fresh PostgreSQL bootstrap and Alembic metadata
drift checks are mandatory evidence before GS-003 can be marked complete.

## Evidence required for GS-003

Completion requires the exact PR head to pass:

- Ruff;
- strict mypy;
- fresh PostgreSQL migration bootstrap;
- Alembic metadata drift;
- full backend regression including binding/API/provider/worker adversarial
  tests;
- frontend CI;
- real Browser E2E CI.

The merged SHA and exact CI run IDs must be recorded in master tracker issue
#4. No live Google request, deployment or Retail mutation is part of GS-003.
