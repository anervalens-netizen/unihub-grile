# M3 obsolete/dead code cleanup

Tracker: `BE-012`

This cleanup removes duplicate mutation surfaces only where the current product
contract and test coverage prove that a canonical replacement already exists.
It deliberately does not delete something merely because its name comes from an
older implementation stage.

## Removed

### Duplicate calendar write APIs

Removed from the mounted HTTP surface:

- `POST /months/{month_id}/assignments`
- `POST /months/{month_id}/calendar/apply`

Both were compatibility wrappers over `CalendarService.apply`. The standalone
product has two intentional calendar mutation paths:

1. `POST /months/{month_id}/program/cell` for interactive edits;
2. signed `POST /months/{month_id}/schedule/apply` for XLSX apply.

These paths already own revision/CAS, effective resource scope, complete derived
projection updates and transactional append-only audit. Keeping additional
compatibility writes only multiplied the authorization, closed-month, audit and
error-contract surface without adding a product capability.

Assignment and coverage **reads** remain mounted because they are useful scoped
diagnostics and do not create a second business authority.

### Duplicate assignment persistence helpers

`AssignmentRepository` is now read/diagnostic-only. The old direct mutation
helpers were removed because production calendar mutation is owned by
`CalendarService`. This prevents future code from accidentally bypassing:

- month revision/CAS;
- CLOSED-state enforcement;
- Pontaj materialization;
- sales attribution rebuild;
- canonical calendar audit.

AC-02 remains DB-enforced by the partial unique indexes and separately tested at
DB/domain level.

## Coverage retained after removal

The tests continue to prove:

- Program cell writes produce canonical audit metadata;
- signed XLSX apply produces canonical audit metadata;
- CLOSED months reject both canonical mutation paths without state/audit drift;
- AC-02 store/day and person/day invariants remain DB/domain protected;
- OpenAPI contains no legacy duplicate POST routes while assignment/coverage
  readback remains available.

Full Backend CI, fresh PostgreSQL migration/bootstrap, `alembic check`, M3
performance budgets and Frontend CI remain mandatory before merge.

## Reviewed but intentionally retained

### `sales_person_day` / `SalesPersonDay`

The current financial attribution implementation uses the revisioned
`sales_person_day_projections` model. The older `sales_person_day` schema object
is not removed in BE-012 because dropping a historical persisted table is a
data-destructive migration. No measured performance or correctness problem
requires that destructive step. It can be handled later only with explicit data
retention/migration evidence.

### Stage-named API modules (`s4.py`, `s5a.py`, `s5b.py`)

The filenames are historical, but their routes are still active manager,
Google/E-pay and export/canary functionality. Renaming or deleting them here
would be cosmetic churn rather than dead-code removal and would expand BE-012
without correctness value.
