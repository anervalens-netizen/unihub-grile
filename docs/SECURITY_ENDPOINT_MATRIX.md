# Security endpoint matrix

Status: canonical authorization inventory for issue #4 `SEC-001`.

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
- `epay.write` — record E-pay readback for an allowed store.
- `sheet.read` — read projection/canary state within store scope.
- `sheet.sync` — enqueue Sheet projection for an allowed store.
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
| assignments / program / coverage / pontaj | `schedule.read` or `schedule.write` | month + effective store/person scope for business date | manager effective scope; admin tenant-wide |
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
| Sheet projection read | `sheet.read` | requested store | manager effective scope; admin tenant-wide |
| Sheet projection enqueue | `sheet.sync` | requested store | admin in current product policy; resource scope mandatory |
| export/store | `export.create` | requested store | admin currently; future manager policy may grant scoped export |
| export/bulk, pontaj-only | `export.create` | explicit store set; when omitted resolve to caller-visible set, never implicit tenant-wide for manager | admin currently |
| export status/download | `export.read` | persisted export run + embedded requested store set | manager may read only if entire run is in current/effective allowed scope; admin tenant-wide |
| canary/readback | `sheet.read` | explicit store or caller-visible store set | manager scoped; admin tenant-wide |
| `/ingest/fixture` | `admin.fixture` | development fixture tenant | dev/test/ci only; route absent in prod |
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
12. Closed-month state remains an independent business write gate after authorization succeeds.
13. New routes must update this matrix and authorization tests in the same PR.

## Remaining M1 audit targets

- legacy assignments/schedule route families still need complete migration from role helpers to the central capability boundary;
- every historical person/date read must use effective-dated store semantics rather than mutable current catalog state;
- frontend capability rendering remains a usability task and does not replace backend enforcement;
- calendar audit completeness and all closed-month authoritative write paths remain separate financial-correctness gates.

This inventory is normative from the M1 program onward and replaces stage-era assumptions about deferred auth wiring.
