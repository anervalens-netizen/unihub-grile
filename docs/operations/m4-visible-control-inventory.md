# M4 visible-control inventory

Tracker task: `FE-001` — inventory every visible control and map it to a real backend capability/action.

Baseline audited: `main@ab98a16f09e802f5eb7d270d27638c8fe4351cff` after BE-012 / M3-GATE.

This is an inventory, not a UI redesign. It records the production UI path that is actually mounted by `App.tsx`. Client-only navigation/filter/state controls are explicitly distinguished from API reads and mutations. Backend authorization remains authoritative; frontend capability checks are usability controls only.

## Status vocabulary

- `LOCAL` — real client-side navigation/filter/state action; no backend mutation is expected.
- `READ` — control selects/navigates context that is backed by real API reads.
- `MUTATION` — control invokes a real backend mutation and is capability-gated in the UI where applicable.
- `DISABLED_READONLY` — rendered control element is intentionally disabled and has no action.
- `GAP` — implementation differs materially from the canonical capability/action contract and needs follow-up.

## Global shell

| ID | Visible control | Frontend behavior / visibility | Backend capability/action | Status |
|---|---|---|---|---|
| `GLOBAL-001` | UniHub Grile brand button | `navigate("overview")`; always rendered after session shell loads | No direct API call; destination Overview requires `schedule.read` | `LOCAL` |
| `GLOBAL-002` | Hub nav | Visible with `schedule.read`; `navigate("overview")` | Overview reads `/months/{id}/overview` (`schedule.read`) and `/catalog/stores` | `READ` |
| `GLOBAL-003` | Program nav | Visible with `schedule.read`; `navigate("program")` | `/months/{id}/program` → `schedule.read` | `READ` |
| `GLOBAL-004` | Excepții nav | Visible with `schedule.read`; `navigate("exceptions")` | `/months/{id}/exceptions` → `schedule.read` | `READ` |
| `GLOBAL-005` | Joburi nav | Visible with `jobs.read`; `navigate("jobs")` | `/worker/jobs/diagnostics` → `jobs.read` | `READ` |
| `GLOBAL-006` | Management nav | Visible with `month.close.read`; `navigate("close")` | checklist/audit reads → `month.close.read`; mutations below use separate close/reopen capabilities | `READ` |

App bootstrap also performs `/session` and `/months`; the latter is explicitly guarded by `month.read`. Route rendering is gated by `requiredCapabilitiesForRoute()` before a page is mounted.

## Hub / Overview

| ID | Visible control | Frontend behavior | Backend capability/action | Status |
|---|---|---|---|---|
| `OV-001` | Luna selector | Changes `monthId` and reloads Overview/store catalog | `GET /months/{id}/overview` → `schedule.read`; `GET /catalog/stores` → scoped authenticated catalog read | `READ` + `GAP-01` |
| `OV-002` | Program | `navigate("program")` | Destination program read → `schedule.read` | `LOCAL/READ` |
| `OV-003` | Excepții | `navigate("exceptions")` | Destination exceptions read → `schedule.read` | `LOCAL/READ` |
| `OV-004` | Store-name row link | `navigate("store", store.id)` | Store screen loads program/pontaj/catalog/attribution/grid/E-pay/Sheet reads; route requires `catalog.read`, `schedule.read`, `grid.read`, `epay.read`, `sheet.read` | `LOCAL/READ` |
| `OV-005` | Deschide | Same destination as store-name row | Same as `OV-004` | `LOCAL/READ` |
| `OV-006` | Necesită atenție item | Store issue → store route; otherwise Exceptions route | Store read set above or `/months/{id}/exceptions` → `schedule.read` | `LOCAL/READ` |

## Program

| ID | Visible control | Frontend behavior / visibility | Backend capability/action | Status |
|---|---|---|---|---|
| `PG-001` | Luna selector | Changes month and reloads matrix | `GET /months/{id}/program?perspective=...` → `schedule.read` | `READ` |
| `PG-002` | Per magazin | Client perspective state → reload | Same Program GET with `perspective=stores` → `schedule.read` | `READ` |
| `PG-003` | Per agent | Client perspective state → reload | Same Program GET with `perspective=people` → `schedule.read` | `READ` |
| `PG-004` | Editable day cell | Enabled only when `schedule.write` and cell not backend-projected as locked; opens editor | No mutation until Save; backend save authority is `/program/cell` | `LOCAL/MUTATION-context` |
| `PG-005` | Agent selector | Edits local draft | Save payload `person_id` → `/months/{id}/program/cell` → `schedule.write` | `LOCAL/MUTATION-context` |
| `PG-006` | Status selector | Edits local draft | Save payload `status` → same endpoint/capability | `LOCAL/MUTATION-context` |
| `PG-007` | Magazin selector | Edits local draft; disabled unless WORKING | Save payload `store_id` → same endpoint/capability | `LOCAL/MUTATION-context` |
| `PG-008` | Tip zi selector | Edits local draft; disabled unless WORKING | Save payload `working_kind` → same endpoint/capability | `LOCAL/MUTATION-context` |
| `PG-009` | Anulează | Discards local editor state | No backend action | `LOCAL` |
| `PG-010` | Salvează | Visible editor exists only through `schedule.write`; disabled while saving | `POST /months/{id}/program/cell?expected_revision=...` → `schedule.write` → `CalendarService.apply` + CAS/scope/audit | `MUTATION` |

The backend exposes `GET /months/{id}/program/choices` (`schedule.read`) as a scoped choice contract, but the current editor derives people/stores from the already loaded matrix instead of consuming that endpoint. Save remains server-authorized and scope-validated; this is recorded as `FOLLOWUP-01`, not a fake mutation.

## Excepții

| ID | Visible control | Frontend behavior | Backend capability/action | Status |
|---|---|---|---|---|
| `EX-001` | Luna selector | Changes month and reloads exception list | `GET /months/{id}/exceptions` → `schedule.read` | `READ` |
| `EX-002` | Toate | Client-only list filter | No API action | `LOCAL` |
| `EX-003` | Doar blocante | Client-only filter on `blocking_close` | No API action | `LOCAL` |

Exception `action_hint` text is display-only; it is not rendered as an actionable control.

## Management / Close

The route itself requires `month.close.read`. In the current backend role map only ADMIN receives that capability, and ADMIN receives all capabilities, so the currently mounted page cannot expose close/reopen buttons to a Manager/READONLY principal. Backend mutations still enforce their own distinct capabilities.

| ID | Visible control | Frontend behavior | Backend capability/action | Status |
|---|---|---|---|---|
| `MGMT-001` | Luna selector | Reloads checklist and timeline | `GET /months/{id}/close-checklist` + `/close-events` → `month.close.read` | `READ` |
| `MGMT-002` | Pregătește închiderea | Opens local confirmation; disabled on blockers/CLOSED | No mutation yet | `LOCAL` |
| `MGMT-003` | Confirmă închiderea | Sends expected revision | `POST /months/{id}/close` → `month.close` → policy close/audit | `MUTATION` |
| `MGMT-004` | Renunță | Closes confirmation | No API action | `LOCAL` |
| `MGMT-005` | Motiv textarea | Local reopen reason; minimum-length validation | Payload for reopen | `LOCAL/MUTATION-context` |
| `MGMT-006` | Reopen | Enabled after valid reason | `POST /months/{id}/reopen-admin` → `month.reopen` → policy reopen/audit | `MUTATION` |

`MGMT-003`/`MGMT-006` rely on route-level visibility rather than separate frontend checks for `month.close`/`month.reopen`. This is safe under the current fixed role→capability map but should be made capability-explicit if capabilities become independently grantable; see `FOLLOWUP-02`.

## Joburi

| ID | Visible control | Frontend behavior | Backend capability/action | Status |
|---|---|---|---|---|
| `JOBS-001` | Actualizează | Re-runs diagnostics query; disabled while loading | `GET /worker/jobs/diagnostics?terminal_limit=50` → `jobs.read`, tenant/resource scoped | `READ` |

No retry/cancel/run mutation is presented in the current Jobs UI.

## Magazin

The store route requires `catalog.read`, `schedule.read`, `grid.read`, `epay.read`, and `sheet.read` before it mounts.

Initial/month-change read set:
- `GET /months/{id}/program?perspective=people` → `schedule.read`
- `GET /months/{id}/pontaj-totals` → `schedule.read`
- `GET /catalog/stores` → scoped authenticated catalog read (`GAP-01`)
- `GET /catalog/people?store_id=...` → scoped authenticated catalog read (`GAP-01`)
- `GET /months/{id}/attribution` → `grid.read`
- `GET /months/{id}/grid` → `grid.read`
- `GET /months/{id}/epay/freshness?store_id=...` → `epay.read`
- `GET /months/{id}/sheet-projection?store_id=...` → `sheet.read`

| ID | Visible control | Frontend behavior / visibility | Backend capability/action | Status |
|---|---|---|---|---|
| `STORE-001` | ← back | `navigate("overview")` | No direct API mutation | `LOCAL` |
| `STORE-002` | Luna selector | Changes month and reloads full store read set above | Real scoped reads | `READ` |
| `STORE-003` | Control tab | Local tab state | No API action | `LOCAL` |
| `STORE-004` | Calendar tab | Local tab state | No API action | `LOCAL` |
| `STORE-005` | Grilă & Pontaj tab | Local tab state | No API action | `LOCAL` |
| `STORE-006` | Agent performance row | `navigate("agent", person.id)` | Agent screen reads program/attribution/grid/E-pay/Sheet; route requires `schedule.read`, `grid.read`, `epay.read`, `sheet.read` | `LOCAL/READ` |
| `STORE-007` | Editează calendarul | Visible only with `schedule.write`; switches to Calendar tab | Save authority remains `/program/cell` | `LOCAL/MUTATION-context` |
| `STORE-008` | Sincronizează Sheet | Visible only with `sheet.sync` | `POST /months/{id}/sheet-projection/enqueue` → `sheet.sync`, requested-store scope, durable revision-bound job | `MUTATION` |
| `STORE-009` | Exportă XLSX | Visible only with `export.create` | `POST /months/{id}/export/store` → `export.create`, requested-store scope, durable revision-bound export job | `MUTATION` |
| `STORE-010` | Calendar day cell | Enabled only with `schedule.write` and unlocked cell; opens same editor contract as Program | No mutation until Save | `LOCAL/MUTATION-context` |
| `STORE-011` | Agent selector | Local edit draft | Saved through `/program/cell` → `schedule.write` | `LOCAL/MUTATION-context` |
| `STORE-012` | Status selector | Local edit draft | Saved through `/program/cell` → `schedule.write` | `LOCAL/MUTATION-context` |
| `STORE-013` | Magazin selector | Local edit draft; disabled unless WORKING | Saved through `/program/cell` → `schedule.write` | `LOCAL/MUTATION-context` |
| `STORE-014` | Tip zi selector | Local edit draft; disabled unless WORKING | Saved through `/program/cell` → `schedule.write` | `LOCAL/MUTATION-context` |
| `STORE-015` | Anulează | Discards local edit draft | No API action | `LOCAL` |
| `STORE-016` | Salvează | Available only through schedule-write editor; disabled while saving | `POST /months/{id}/program/cell?expected_revision=...` → `schedule.write` → `CalendarService.apply` + CAS/scope/audit | `MUTATION` |

The Store screen does not present an E-pay write control, grid-compute control, export download control, or arbitrary worker execution control.

## Agent

| ID | Visible control | Frontend behavior | Backend capability/action | Status |
|---|---|---|---|---|
| `AGENT-001` | Luna selector | Reloads agent read model | Program → `schedule.read`; attribution/grid → `grid.read`; E-pay → `epay.read`; Sheet → `sheet.read` | `READ` |
| `AGENT-002` | Calendar day cells | `ProgramMatrix` is mounted without `onCellClick`, so every day button is disabled | No action; intentionally read-only | `DISABLED_READONLY` |

No agent mutation control is mounted.

## Findings requiring follow-up

### GAP-01 — catalog routes do not explicitly enforce `catalog.read`

Canonical `docs/SECURITY_ENDPOINT_MATRIX.md` says every authenticated route must have an explicit capability and maps `/catalog/*` to `catalog.read`. `backend/src/ugrile/api/catalog.py` scopes stores/people by tenant/effective store and requires an authenticated principal, but `GET /catalog/stores` and `GET /catalog/people` do not call `authorize(..., Capability.CATALOG_READ)`.

Current exploitability is limited because ADMIN, MANAGER, and READONLY all receive `catalog.read` in the fixed role map. It is still contract drift and violates the matrix invariant that absence of explicit capability enforcement is a defect. FE-001 records it; a focused backend/security task should correct it rather than changing the canonical matrix to match weaker implementation.

### FOLLOWUP-01 — editor choices bypass the existing scoped choice endpoint

Program/Store editors construct selectable people/stores from the loaded grid/catalog instead of using `GET /months/{id}/program/choices`. The mutation itself is real and safe because `/program/cell` re-authorizes scope and applies CAS/audit, but the choice UI can be incomplete/stale relative to the backend's dedicated choice contract. This belongs to M4 operational-completeness work.

### FOLLOWUP-02 — Management action visibility is role-coupled

Close/Reopen controls are not individually tested against `month.close` / `month.reopen` in the frontend. They are currently protected indirectly because the whole Management route requires admin-only `month.close.read`, and ADMIN owns every capability. Preserve backend enforcement regardless; add explicit action-level checks before any future independently grantable capability model.

## FE-001 conclusion

- All interactive controls in the currently mounted production frontend path are accounted for above.
- No visible production control was found that invokes a nonexistent API mutation or the removed legacy `/calendar/apply` / POST `/assignments` paths.
- Calendar mutation controls converge on canonical `/program/cell` → `CalendarService.apply`.
- Sheet sync and store XLSX export controls enqueue real durable backend jobs and are capability-gated.
- Client-only navigation/filter/tab/cancel/confirmation controls are identified as local behavior rather than falsely described as backend actions.
- `GAP-01` is the only direct capability-contract mismatch found by this inventory; `FOLLOWUP-01` and `FOLLOWUP-02` are operational-hardening items rather than fake actions.
