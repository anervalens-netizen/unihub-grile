# Security endpoint matrix

Status: canonical authorization inventory for issue #4 `SEC-001`; M1 route-family re-attestation completed through PR #26.

Every authenticated UniHub Grile API route must have an explicit capability and resource scope. Absence from this matrix is a defect, not implicit permission.

## Capability vocabulary

- `catalog.read` — read tenant-scoped store/person catalog.
- `schedule.read` — read program/calendar/pontaj/coverage.
- `schedule.write` — mutate program/calendar/import schedule within effective scope.
- `grid.read` — read attribution/grid calculations within scope.
- `grid.compute` — recompute authoritative payroll grid snapshots; admin-only in the current policy.
- `payroll.master.read` — read salary/tickets/flip master windows; admin-only.
- `payroll.master.write` — mutate salary/tickets/flip master windows; admin-only and blocked while the selected month is CLOSED.
- `holiday.read` — read informational holiday markers.
- `holiday.write` — mutate holiday calendars/overrides; admin-only.
- `epay.read` — read E-pay freshness/readback state within store scope.
- `epay.write` — record E-pay readback for an allowed store, including an authenticated Google Sheet read whose values are persisted locally.
- `sheet.read` — read projection/canary/reconciliation state within store scope.
- `sheet.sync` — enqueue Sheet projection for an allowed store.
- `sheet.bind` — read/create/rebind the permanent store↔Google-Sheet identity; admin-only.
- `export.read` — read/download export runs only when their requested store set is within scope.
- `export.create` — enqueue exports for an allowed store set.
- `month.read` — read ordinary tenant month metadata.
- `month.close.read` — read close checklist and close/reopen audit history; admin-only in the current product contract.
- `month.close` — close a month; admin-only in the current product contract.
- `month.reopen` — reopen a month with reason; admin-only.
- `admin.fixture` — development/test fixture bootstrap only; never mounted in prod.
- `jobs.read` — read tenant-scoped async job diagnostics, further restricted by embedded resource scope where applicable.

Admins are tenant-wide unless a more restrictive product rule applies. Managers are deny-by-default outside effective-dated store scope. READONLY principals never receive mutation capabilities.

## Route families

| Route family | Capability | Resource boundary | Target rule |
|---|---|---|---|
| `/catalog/*` | `catalog.read` | tenant; managers receive only visible resources where route returns store/person rows | authenticated tenant principal |
| `/months`, month metadata | `month.read` | tenant | authenticated principal |
| assignment readback / coverage / program / pontaj | `schedule.read` | month + effective store/person scope for business date | manager effective scope; admin tenant-wide |
| Program cell mutation | `schedule.write` | month + effective store/person scope for business date + revision/CAS | manager effective scope; admin tenant-wide |
| schedule XLSX template/preview/apply | `schedule.read` / `schedule.write` | signed contract scope + current effective scope | manager scope; admin tenant-wide |
| attribution / grid / store-agent drilldown | `grid.read` | month + store/person effective scope; returned aggregates/anomalies must be scoped too | manager scope; admin tenant-wide |
| grid recompute | `grid.compute` | tenant month | admin-only |
| salary/payroll master read | `payroll.master.read` | tenant payroll data | admin-only |
| salary/payroll master write | `payroll.master.write` | tenant person + selected month write gate | admin-only; CLOSED month rejects write |
| holiday read | `holiday.read` | tenant month | authenticated role with capability |
| holiday write/override | `holiday.write` | tenant month | admin-only |
| close checklist / audit | `month.close.read` | tenant month | admin-only |
| close | `month.close` | tenant month | admin-only |
| reopen | `month.reopen` | tenant month | admin-only + reason |
| E-pay freshness | `epay.read` | requested store | manager effective scope; admin tenant-wide |
| E-pay readback | `epay.write` | requested store and submitted working agents | admin in current UI policy; resource scope still mandatory |
| Google E-pay readback | `epay.write` | requested month + exact store + current binding/revision/working-person set | admin in current policy; authorization occurs before provider I/O; CLOSED month and post-read identity drift fail closed |
| Sheet binding GET/PUT | `sheet.bind` | tenant + exact store; spreadsheet id globally unique | admin-only; rebind requires CAS identity + reason |
| Sheet projection read | `sheet.read` | requested month + exact store; only a snapshot carrying matching `metadata.month_id` is eligible | manager effective scope; admin tenant-wide |
| Sheet reconciliation read | `sheet.read` | requested month + exact store; legacy/unscoped snapshots fail closed | manager effective scope; admin tenant-wide |
| Sheet projection enqueue | `sheet.sync` | requested store + pinned Sheet identity | admin in current product policy; resource scope mandatory |
| export/store | `export.create` | requested store | admin currently; future manager policy may grant scoped export |
| export/bulk, pontaj-only | `export.create` | explicit store set; when omitted resolve to caller-visible set, never implicit tenant-wide for manager | admin currently |
| export status/download | `export.read` | persisted export run + embedded requested store set | manager may read only if entire run is in current/effective allowed scope; admin tenant-wide |
| canary/readback | `sheet.read` | explicit store or caller-visible store set | manager scoped; admin tenant-wide |
| `/ingest/fixture` | `admin.fixture` | development fixture tenant + locked financial periods touched by payload | dev/test/ci only; route absent in prod; CLOSED touched period rejects ingest |
| `/worker/jobs*` | `jobs.read` | tenant and job resource scope | authenticated admin/manager as defined by job payload/owner policy |
| health/readiness/version | public probe | no business data | no identity dependency |

## Enforcement invariants

1. Tenant match alone is never sufficient for a store/person-scoped route.
2. A manager request with no active effective scope row is denied.
3. Store-list requests must validate every requested store; filtering silently to a subset is forbidden for writes/exports.
4. Explicit out-of-scope resource identifiers fail; broad scoped reads may filter to the caller-visible set.
5. Aggregate totals and anomaly metadata must be computed from the same visible rows as the returned detail rows.
6. Payroll master values are not exposed to managers merely because they can read calculated grid outcomes.
7. Export/job readback is authorized from persisted resource metadata, not request query parameters.
8. Frontend capability rendering is usability only; backend authorization is the security boundary.
9. Development principal headers are an explicit provider and are rejected by production configuration.
10. Fixture routes are not mounted in production.
11. Cross-tenant identifiers return deny/not-found without exposing foreign data.
12. Closed-month state remains an independent business write gate after authorization succeeds. Connector ingest locks the same Month rows before authoritative financial input writes so it serializes with close/reopen.
13. New routes must update this matrix and authorization tests in the same PR.
14. A store has at most one permanent Sheet binding and one Google spreadsheet id may not be bound to multiple stores, including across tenants.
15. Sheet projection jobs pin the binding identity seen at enqueue. A later rebind must make the older job fail terminally before provider I/O; it must never redirect that job to the replacement Sheet.
16. Projection publication may advance `generation`, but live projection is never allowed to create/discover/rebind a Sheet identity as a side effect.
17. Month-scoped Sheet projection/reconciliation reads must prove exact `metadata.month_id`; a successful snapshot for one month and historical snapshots without month identity are not valid evidence for another requested month.
18. Google E-pay readback must authorize `epay.write` and exact store scope before any provider I/O, then revalidate binding identity, calendar-data revision and exact working-person set under locks before local persistence. Ambiguous/stale structure becomes invalid current evidence; it is never silently mapped to zero or accepted from another layout.
19. The managed Google protection contract may expose only the exact current E-pay value cells; it must not delete unrelated external protections merely to make those cells editable.

## M1 re-attestation result

- assignment/coverage readback remains scope-aware; business calendar writes have one interactive authority (`/program/cell`) plus the signed XLSX apply path, both using the central capability boundary and effective-dated person/store scope;
- Program/Overview/Exceptions/attribution manager reads were re-attested and corrected to derive detail, aggregates and anomaly metadata from the same visible resource set;
- historical Program/Pontaj/XLSX paths prefer effective-dated home-store history and fail closed on dated-history gaps or ambiguous monthly ownership instead of trusting mutable current catalog state;
- close checklist and close/reopen audit history are administrative surfaces guarded by `month.close.read`, not regionally scoped manager diagnostics;
- calendar/program business mutations converge on `CalendarService.apply` and its transactional append-only audit event; CLOSED calendar, grid, holiday, salary, E-pay and connector-financial writes fail closed;
- real PostgreSQL tests cover concurrent close and concurrent reopen serialization on the Month row plus digest-verifiable lifecycle history;
- frontend capability rendering remains `SEC-010` / `FE-011`; it is a usability requirement and does not replace backend enforcement;
- API error-envelope normalization remains `BE-008`; envelope inconsistency does not weaken capability, scope, or CLOSED-state enforcement.

This inventory is normative from the M1 program onward and replaces stage-era assumptions about deferred auth wiring.
