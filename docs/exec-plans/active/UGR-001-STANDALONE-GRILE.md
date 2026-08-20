# UGR-001 - Aplicație standalone UniHub Grile

Status: ACTIVE  
Overall outcome: UNVERIFIED  
Owner: primary reviewer + builders desemnați de utilizator  
Created: 2026-08-20  
Updated: 2026-08-20

## Objective

Livrarea unei aplicații standalone, performante și ușor de folosit prin care
managerii controlează programul, pontajul derivat, suplimentarele, atribuirea
vânzărilor și grilele, iar agenții primesc Google Sheets aproape read-only, cu
singurele intrări reprezentate de cele două cantități E-pay per agent.

Aplicația trebuie să poată fi dezvoltată și validată separat de Retail, apoi
integrată printr-un contract versionat numai după ce trece un pilot shadow.

## Non-goals and limits

- Nu modificăm `/opt/Mobiup/unihub-retail` în Stage 1–6.
- Nu modificăm V1, V2 pilot, registry-ul sau Google Sheets live din
  `/opt/Mobiup/grile-salarii` în Stage 1–5.
- Nu dezvoltăm cont/editare pentru agent, schimb de tură, ture multiple,
  pontaj biometric sau editare bidirecțională generală a Sheets.
- Nu folosim numele ca identitate și nu expunem date din toată rețeaua în foaia
  unui magazin.
- Nu citim direct schema Retail ca arhitectură finală.
- Nu facem deploy public sau mutații Google live fără un gate separat și canary.
- Nu inventăm formula salarială, orele/pauzele, sărbătorile sau autoritatea de
  close rămase de confirmat în `docs/PRODUCT_CONTRACT.md`.

## Sources

- `README.md`
- `ARCHITECTURE.md`
- `docs/PRODUCT_CONTRACT.md`
- `docs/UX_EXCEL_SPEC.md`
- deciziile utilizatorului din 2026-08-20, consolidate în documentele de mai sus;
- cele trei capturi furnizate, folosite numai ca referință vizuală și descrise în
  contract; fișierele nu sunt copiate în repository;
- legacy read-only: `/opt/Mobiup/grile-salarii` la commit
  `c131c21cb29131d0dfa0d6b986a0831e19549d5f`;
- Retail read-only la baseline: `/opt/Mobiup/unihub-retail` commit
  `6a3e71484e3f2e399c2fc6db42a98ab1ba8c0251` pe branch-ul activ observat
  `phase-b3b-final-correction-20260819`; se poate schimba înainte de Stage 7.

## Baseline

- Repository root: `/opt/UniHub/unihub-grile`
- Branch and exact baseline: `main` / tag-ul adnotat
  `planning-baseline-20260820`; rezolvă SHA-ul cu
  `git rev-parse planning-baseline-20260820^{commit}` înainte de lucru
- Working tree: clean la crearea tag-ului de baseline
- Protected user/newer work: none în repository-ul nou
- Relevant runtime/deployed state: niciun serviciu, DB, Google binding sau rută
  publică; toate sunt `NOT APPLICABLE` până la etapele lor
- Host baseline: `dell-standby`; deploy-ul țintă se va decide numai la Stage 6
- Existing live state: operațiunile Grile active rămân în Retail; standalone-ul
  legacy este retras și nu trebuie reactivat accidental

Tag-ul de baseline indică exact commitul documentației contractuale de la care
începe Stage 1; builderul trebuie să înregistreze orice drift față de el.

## Minimal code map țintă

- `backend/api/`: FastAPI routers, auth boundary, request/response only.
- `backend/domain/`: reguli pure calendar, pontaj, atribuire, grilă, close.
- `backend/services/`: orchestration transactions and use cases.
- `backend/repositories/`: PostgreSQL persistence only.
- `backend/connectors/`: fixtures, client source v1, Google Sheets.
- `backend/worker/`: one durable worker and typed jobs.
- `backend/migrations/`: schema versionată.
- `frontend/src/`: Overview, Program, Magazin, Agent, Excepții, Close.
- `tests/`: unit/domain, repository/API, import/export, integration.
- `docs/`: product, UX, operations, decisions.

Structura exactă poate fi ajustată în Stage 1, dar separarea de responsabilități
și limitele arhitecturale nu se elimină fără actualizarea planului.

## Acceptance contract

| ID | Observable behavior | Must not change | Verification and expected result | Runtime proof | Evidence | Status |
|---|---|---|---|---|---|---|
| AC-01 | Aplicația standalone pornește local cu API, frontend, PostgreSQL și un singur worker; health/readiness sunt determinate fără Retail/Google | Retail și legacy Grile | build + test + local health; oprirea Retail/Google fixture nu blochează read UI | local compose/systemd dev probe | Independent audit on `153d283d6eacf47b354a07983b7b12cc0a8c4101`: health/readiness, worker entrypoint and single execution authority PASS; Compose startup statically PASS, live re-probe environment-limited by Docker DNS/apt resolution | PASS |
| AC-02 | Schema și motorul impun max. un agent per magazin/zi și max. un magazin per agent/zi | datele fixture | teste concurente + constrângeri DB; conflictele sunt respinse determinist | API conflict 409 | Independent audit on `153d283d6eacf47b354a07983b7b12cc0a8c4101`: real PostgreSQL concurrent races 3/3 PASS, DB indexes and API 200/409/409 probes PASS | PASS |
| AC-03 | Fixture connector versionat furnizează magazine, persoane, targete și vânzări magazin/zi fără import din codul/schema Retail | Stage 1–6 read-only Retail | search/import-boundary test + fixture ingest idempotent | ingest status local | Independent audit on `153d283d6eacf47b354a07983b7b12cc0a8c4101`: versioned targets, idempotent ingest, tenant-safe IDs/FKs, worker tenant payload and zero Retail imports PASS | PASS |
| AC-04 | Managerul poate crea/modifica programul lunar, cu `NORMAL`, `EXTRA_HOME`, `EXTRA_OTHER`, `OFF`, `LEAVE`, scoped la aria sa | fără wizard schimb de tură | API/UI tests; scope 403; revision stale 409 | browser flow fixture | S2 implementation now includes whole-calendar apply, effective-dated manager scopes, all five statuses and home/other validation; independent gate pending. | UNVERIFIED |
| AC-05 | Orice modificare a calendarului actualizează în aceeași revizie pontajul derivat, inclusiv retroactiv la mijlocul lunii | fără editor pontaj separat | domain/API test before/after; total ore consistent | UI + XLSX/Sheet preview | S2 projection engine derives person calendar, store coverage and configurable-hours Pontaj in the same CAS transaction; independent gate pending. | UNVERIFIED |
| AC-06 | Modelul XLSX prepopulat poate fi preview-uit și aplicat atomic; conflictele, scope-ul și revision stale nu produc scrieri parțiale | nu creează persoane/magazine | parser round-trip, malformed/property tests, rollback test | browser upload preview/apply | S2 template/Manifest, hidden technical IDs, protected dropdown cells, parser errors, preview and atomic apply endpoints are implemented; independent gate pending. | UNVERIFIED |
| AC-07 | Întreaga vânzare a magazinului/zi este creditată agentului din calendar; totalurile fizice nu se dublează la reatribuire | sursa fizică imuabilă | hash/totals tests before/after reassignment | drill-down magazin/agent | deferred to S3. | UNVERIFIED |
| AC-08 | `EXTRA_HOME` și `EXTRA_OTHER` sunt clasificate corect și au efect separat, fără copiere de vânzări | un agent/store/zi | table-driven domain tests și excepții invalid home/other | calendar + grid preview | domain preconditions implemented in `ugrile.domain.calendar.validate_working_kind`; full attribution is S3. | UNVERIFIED |
| AC-09 | Calculul grilei este determinist, versionat și reconciliabil pentru două persoane/magazin | formula neconfirmată rămâne config/UNVERIFIED | golden fixtures vs contract acceptat, Decimal, unchanged input hash -> same output | store/agent detail | deferred to S3 (formula confirmation pending). | UNVERIFIED |
| AC-10 | Overview, filtrele, Program, Magazin, Agent și Excepții sunt coerente și utilizabile la 75+ magazine | fără Google I/O în web reads | component/e2e + query-count/perf; no N+1 | browser desktop/tablet/mobile | deferred to S4 (UI). | UNVERIFIED |
| AC-11 | TL vede/scrie numai aria effective-dated; admin vede tot; closed month respinge writes | auth real se leagă ulterior | API authorization matrix | authenticated local/staging flow | `X-Ugrile-Identity` + `X-Ugrile-Tenant` remain the dev/test auth seam; S2 adds tenant-safe, effective-dated `manager_scopes`, deny-by-default manager writes and closed-month rejection. Independent gate pending. | UNVERIFIED |
| AC-12 | Google canary primește numai date locale, grilă/calendar și Pontaj compatibil vizual; ultima proiecție bună rămâne la eșec | zero live sheets înainte de aprobare | fake adapter tests, structural readback, one copied canary | Google canary readback | deferred to S5. | UNVERIFIED |
| AC-13 | Numai cele patru dropdown-uri E-pay sunt editabile; valorile 0..10 sunt ingerate și auditate, invalidul păstrează last-good | fără inbound general | protection/read tests + blank/text/fraction/11 cases + close readback | Google canary edit/read | schema + check constraint in place (`epay_value_range`, `epay_category_enum`); inbound adapter is S5. | UNVERIFIED |
| AC-14 | Exportul per magazin are `Grila`+`Pontaj`; bulk ZIP și pontaj-only respectă filtrele, manifestul și nu au external links | nu exportă alte arii | parse/render/round-trip + checksum tests | download browser, render sample | deferred to S5. | UNVERIFIED |
| AC-15 | Close validează toate blocantele și îngheață luna; reopen este admin-only, motivat și auditat append-only | niciun overwrite de istoric | transaction/concurrency/audit tests | close/reopen browser flow | partial: month state machine + revision bump + closed-state rejection are in S1. Reopen and the audit chain land in S3. | UNVERIFIED |
| AC-16 | p95 overview <500 ms pilot/<1 s target; save DB <500 ms; Google/export async; jobs durable/idempotent | fără polling Google frecvent | benchmark/query count, restart/retry tests | local/staging measurements | partial: in-process worker with `SKIP LOCKED` semantics and idempotency_key. Benchmarks and a separate worker container are S4+. | UNVERIFIED |
| AC-17 | Un pilot shadow complet reconciliază program, pontaj, vânzări, E-pay, grile și exporturi fără a modifica V1/V2 live | V1/V2 live unchanged | manifest per store/day/person + explained zero/unresolved diffs | copied canaries only | deferred to S6. | UNVERIFIED |
| AC-18 | Integrarea Retail folosește contract versionat și capabilitate optională; Grile rămâne deployabil fără Retail | fără shared DB schema final | contract tests + Retail-off probe + exact integration diff | staging then formal Retail gate | deferred to S7. | UNVERIFIED |

## Tasks and dependencies

| Stage | Scope / livrabil mare | Depends on | Owner | Attempts | State |
|---|---|---|---|---:|---|
| S1 | Foundation standalone: stack, schema, domain invariants, fixture connector, auth/scopes skeleton, one worker, local dev/test | none | builder 1 | 2 | PASS |
| S2 | Calendar + pontaj + XLSX schedule import: APIs, revisions, derived projections, preview/apply atomic | S1 GO | builder 2 | 1 | BUILDING |
| S3 | Sales attribution + supplementary classification + rule-pack/grid engine + close/reopen core | S2 GO + formula confirmations | builder 3 | 0 | BACKLOG |
| S4 | Manager UI complete: Overview, Program, Store, Agent, Exceptions, Close, responsive/performance | S3 GO | builder 4 | 0 | BACKLOG |
| S5 | Google adapter + bounded E-pay inbound + XLSX exports + copied canary | S4 GO + Google canary authority | builder 5 | 0 | BACKLOG |
| S6 | Shadow pilot, reconciliation, observability, backup/runbook, production-readiness verdict | S5 GO | builder 6 | 0 | BACKLOG |
| S7 | Versioned Retail integration, optional navigation/auth, formal migration/cutover | S6 GO + Retail stable + explicit opening | future | 0 | BACKLOG |

Stages are sequential because schema, financial rules, state management and UI
are coupled. Read-only research may run in parallel; shared writes may not.

## Stage contracts and handoffs

### S1 — Foundation standalone

Builder scope:

- choose and pin the minimal stack described in `AGENTS.md`;
- create app/API/frontend/DB/worker skeleton and deterministic local fixtures;
- implement tenant, store, people, assignment, month, site-day schedule schema;
- enforce AC-02 at both domain and DB transaction boundaries;
- define connector v1 types and fixture ingest; no Retail import/database access;
- establish local commands for format/typecheck/test/build/health;
- add only documentation changed by actual implementation.

Required handoff: exact commit, `git status`, commands and raw summaries,
migration/schema evidence, API conflict probes, health/readiness, known gaps.
Do not start UI product screens or Google integration.

S1 gate: AC-01, AC-02, AC-03; parts of AC-11/16 may remain UNVERIFIED.

### S2 — Calendar, pontaj, import program

Builder scope:

- implement revisioned calendar use cases and manager scope;
- derive person calendar, store coverage and Pontaj from one authority;
- implement XLSX template, preview, error report and atomic CAS apply;
- prove mid-month edits update Pontaj in the same revision;
- keep hours/pause configurable and document any still-unconfirmed value.

Required handoff: exact commit, conflict/rollback tests, at least one rendered
template, parsed round-trip results, API evidence, no Google/live data.

S2 gate: AC-04, AC-05, AC-06 and relevant AC-11.

### S3 — Sales, suplimentare, calcul, close core

Precondition: owner confirms remaining formula, hours/pause, holidays and close
authority listed in `docs/PRODUCT_CONTRACT.md`.

Builder scope:

- ingest immutable store-day sales via connector generation;
- attribute full store-day total from calendar;
- implement `EXTRA_HOME`/`EXTRA_OTHER` without company-total duplication;
- implement Mobiup rule pack with Decimal and versioned input/output hashes;
- implement close checks, immutable close and audited reopen;
- add golden fixtures representative of home/other/leave/missing source.

Required handoff: exact commit, input/output business fixtures, hashes and total
reconciliation, concurrency/close tests, explicit formula provenance.

S3 gate: AC-07, AC-08, AC-09, AC-15.

### S4 — Manager UI

Builder scope:

- implement surfaces and interactions from `docs/UX_EXCEL_SPEC.md`;
- avoid N+1 and Google calls; use monthly read models;
- provide desktop/tablet/mobile behavior and accessible keyboard/focus/error
  states;
- test 75+ stores/31 days and mid-month edits;
- no cosmetic redesign of the Sheet contract.

Required handoff: exact commit, browser paths, screenshots with fixture names
only, query counts, perf measurements, build/type/test results.

S4 gate: AC-10, UI portions of AC-04/11/15, AC-16 web targets.

### S5 — Sheets, E-pay, exports

Builder scope:

- implement fake adapter first, then exactly one copied canary after authority;
- write store-local Grila and Pontaj projections, with generation/readback;
- protect all cells except four E-pay dropdowns; ingest valid 0..10 values;
- implement per-store, bulk and pontaj-only exports and manifests;
- render/inspect XLSX and Google canary against accepted visual contract;
- retain last-good projection on provider failure.

Required handoff: exact commit, fake-adapter tests, sanitized canary ID reference
outside Git, structural/protection readback counts, rendered samples, export
checksums and parse results. No permanent/live store sheet mutations.

S5 gate: AC-12, AC-13, AC-14, async portions of AC-16.

### S6 — Shadow pilot and production readiness

Builder scope:

- choose a bounded cohort copied from representative firms/managers;
- run at least one full monthly scenario and mid-month correction;
- reconcile store physical totals, personal credit, supplementary days, Pontaj,
  E-pay, grid totals, exports and sync states;
- add metrics, bounded logs, backup/restore and operator runbook;
- produce immutable sanitized manifest and rollback/cutover proposal.

Required handoff: exact commit/artifact, cohort definition, reconciliation counts,
all unresolved diffs, performance, restart/provider-failure evidence, restore
proof, no live V1/V2 changes.

S6 gate: AC-16, AC-17 and regression of AC-01–15. GO means ready to discuss
Stage 7, not permission to cut over.

### S7 — Retail integration and cutover

This stage is intentionally closed. Before opening:

- Retail refactor must be declared stable by its owner;
- current Retail HEAD/runtime and policies must be re-baselined;
- connector contract and auth/navigation integration require a separate exact
  acceptance contract;
- Retail branch/PR/CI/artifact/deploy rules remain mandatory;
- permanent Sheet migration and retirement of old paths require explicit
  approval and rollback evidence.

S7 gate: AC-18 plus a separately approved cutover contract.

## GO/NO-GO procedure after each stage

1. Builder stops after the stage handoff; it does not start the next stage.
2. User returns to the primary reviewer with repository path and exact commit.
3. Reviewer checks status/drift, reads the integrated diff, and runs only the
   stage verification contract plus necessary regression.
4. Reviewer returns:
   - `GO`: all stage criteria PASS; tracker moves next stage to READY;
   - `NO-GO`: concrete defects, largest gap, stage returns BUILDING;
   - `BLOCKED`: exact unavailable dependency/decision.
5. Builder fixes the largest demonstrated gap; no unrelated refactor.

## Progress and transitions

- 2026-08-20: user confirmed manager-only schedule editing, one agent/store/day,
  full store-day attribution, no shift-swap feature, derived dynamic Pontaj,
  E-pay dropdown input, month lock/reopen, scoped TL/admin, XLSX schedule import
  and Grila/Pontaj exports.
- 2026-08-20: screenshots inspected read-only; visual requirements consolidated
  without copying employee-bearing images into Git.
- 2026-08-20: repository and canonical documents created for tag-ul
  `planning-baseline-20260820`. All acceptance criteria remain UNVERIFIED. Next:
  hand S1 to one builder.
- 2026-08-20: S1 builder delivered the foundation. Single coherent commit on
  `main` ahead of `planning-baseline-20260820`. Evidence in
  `.agent/evidence/UGR-001/S1/`; build/test/format/typecheck all clean (28
  backend tests, 2 frontend tests, 0 ruff/mypy/tsc errors). Stage moves from
  `READY` to `BUILDING` and stops — handoff to primary reviewer for the GO/NO-GO
  audit.
- 2026-08-20: primary + independent read-only audit on exact artifact
  `b9e1b3d6343c0e95af9ba1a09a9687875705a423` returned `NO-GO/FAIL` for
  AC-01, AC-02 and AC-03. S1 remains `BUILDING`, attempt 2; S2 stays blocked.
  Targeted checks on the unchanged artifact passed (`28` pytest, ruff, mypy,
  `2` vitest, tsc, Vite build). PostgreSQL migration and sequential API
  conflicts were reproduced independently, so the failure is architectural and
  contractual rather than a generic build failure.
- 2026-08-20: S1 attempt 2 builder delivered the remediation in exact commit
  `153d283d6eacf47b354a07983b7b12cc0a8c4101`. Alembic chain lands as
  `0001_initial_schema` (1e5c879d0851) then
  `0002_composite_tenant_fks_and_targets` (b9fbb01f8cd0); backend pytest
  `36 passed` (33 SQLite + 3 real PostgreSQL integration including concurrent
  AC-02 races); `ruff` and `mypy --strict` clean on 35 source files; frontend
  `tsc` and `pnpm build` clean; `python -m ugrile.worker.worker` runs the
  durable loop and drains the outbox; Compose web proxy is configured for
  `http://api:8080`; evidence re-emitted under `.agent/evidence/UGR-001/S1/`
  is internally consistent against the candidate commit.
- 2026-08-20: GPT-5.6 Luna read-only audit returned GO for S1 attempt 2;
  AC-01/02/03 PASS and S2 moved to READY. Fresh Compose startup in this
  sandbox was limited by external Docker DNS/apt resolution, not a source
  defect.
- 2026-08-20: S2 implementation started from the interrupted partial work and
  was completed without resetting it. Added revisioned whole-calendar CAS apply,
  effective-dated tenant-safe manager scopes, person/store/Pontaj projections,
  protected XLSX template + hidden technical IDs/Manifest, preview/apply routes,
  parser validation and compatibility routing through the calendar authority.
  Backend checks currently pass: `49 pytest`, `ruff`, `mypy --strict`; a fresh
  PostgreSQL 17 container applied the full Alembic chain through
  `c3a1b7e2d4f6`, `alembic check` reports no drift, and `manager_scopes` is
  present. Frontend typecheck/build/vitest remain environment-limited by the
  pre-existing root-owned pnpm store and missing Rollup/Vite optional packages;
  no frontend files changed in S2.
- 2026-08-20: Independent S2 read-only review initially returned `NO-GO` against
  the live uncommitted tree. It identified preview/apply disagreement, invalid
  per-person dropdown values, missing template prepopulation, missing manager
  read filtering, hidden-list protection and migration-index parity. These were
  concrete defects fixed before freeze and must be rechecked against the exact
  S2 commit.

## Attempts, failures, and discoveries

- Initial directory creation inherited root ownership and blocked `git init`.
  Ownership was corrected only inside the new `/opt/UniHub/unihub-grile` path;
  no existing repository or runtime was touched.
- Legacy `Pontaj` was previously manual. The new contract intentionally replaces
  manual Pontaj input with a projection derived from calendar; compatibility is
  visual/output, not ownership.
- Anonymous edit-by-link cannot identify the editor. E-pay audit records the
  Sheet/store source and read time, not an unprovable person identity.
- S1 partial unique index dispatch was originally DB-only. SQLite exposes the
  constraint columns in its error message but not the index name; PostgreSQL
  surfaces the index name in `pgerror.diag.constraint_name`. The repository
  now combines the index name (when present) with a follow-up read of the
  committed state to name the violated invariant precisely across dialects.
  This keeps the API payload deterministic whether the underlying engine is
  SQLite (unit tests) or PostgreSQL (production + canary).
- SQLite in-memory databases are isolated per connection by default. The test
  suite installs a ` ``StaticPool`` so the engine the application uses during
  the TestClient request shares the schema the test seeded. Without that the
  API call surfaces `OperationalError: no such table` even though the test
  fixture committed the rows.
- The Alembic autogenerate produced `1e5c879d0851_0001_initial_schema.py`.
  Renamed to a clean filename `0001_initial_schema.py`; the SHA is preserved
  on the next `alembic` invocation via the head pointer, not the file name.
- Multi-tenant probe applied the same internal store/person codes to
  `tenant_alpha` then `tenant_beta`. Only `tenant_alpha` owned the two Store and
  three Person rows, while `tenant_beta` sales referenced those same IDs. Root
  cause: `make_store_id`/`make_person_id` omit tenant and connector upserts use
  primary-key lookup instead of tenant + internal code.
- `python -m ugrile.worker.worker` exited successfully in about one second and
  left an enqueued NOOP in `PENDING`; the module exports `run_forever` but has no
  executable entrypoint. The tracked worker proof used API `/worker/run`, not the
  dedicated worker process.
- Tracked evidence is internally inconsistent: `test-summary.txt` reports clean
  ruff/mypy while `typecheck-summary.txt` retains an older 129-ruff/32-mypy
  failure. Current independent rerun is clean, but evidence must be replaced by
  one exact-candidate summary.

## Decisions

- Separate service/application, not in-process Retail plugin: independent domain
  and release cadence.
- Calendar is the person-at-site-day authority; POS/TL code is provenance only.
- Full store-day sale is attributed to the calendar person because business
  guarantees one working agent per store/day.
- No general bidirectional Sheet synchronization. Only four bounded E-pay cells
  are read.
- One durable worker handles source ingest, Google and XLSX jobs; no worker per
  feature and no frequent Google polling.
- Pontaj changes whenever the open-month calendar changes; there is no second
  editable attendance truth.
- Git remains mandatory but simple: coherent stage commits, no PR ceremony in
  the standalone repository while work is sequential. Retail Stage 7 follows
  Retail's stricter policy.

## Auditor verdicts

- 2026-08-20 — S1 exact artifact
  `b9e1b3d6343c0e95af9ba1a09a9687875705a423`: **FAIL / NO-GO**.
  - AC-01 FAIL: Compose web proxy defaults to its own container localhost;
    dedicated worker command exits immediately; API also exposes job execution,
    contradicting one worker authority.
  - AC-02 FAIL: correct domain/index/sequential 409 behavior, but no concurrent
    PostgreSQL test required by the contract; referenced integration test absent.
  - AC-03 FAIL: no targets in connector/fixture, worker always applies canonical
    `tenant_fixture`, and global store/person IDs cause cross-tenant references.
  - Independent auditor: `/root/s1_acceptance_audit`, confidence `0.99`.
  - Largest gap: repair AC-03 end-to-end with targets, tenant-scoped identity and
    worker payload/tenant correctness, then prove two-tenant isolation on
    PostgreSQL.

- 2026-08-20 — S1 attempt-2 exact artifact
  `153d283d6eacf47b354a07983b7b12cc0a8c4101`: **PASS / GO**.
  - AC-01 PASS: health/readiness, dedicated worker entrypoint, single execution authority and Compose proxy configuration verified; fresh Compose boot was environment-limited by Docker DNS/apt resolution only.
  - AC-02 PASS: real PostgreSQL concurrent store-day/person-day races and cross-tenant FK test passed 3/3; API conflict probes remain 200/409/409.
  - AC-03 PASS: versioned targets, tenant-scoped IDs/composite FKs, idempotent fixture ingest, worker tenant payload and Retail import boundary all verified.
  - Independent auditor: GPT-5.6 Luna, read-only, confidence high; frontend/Compose replay limitations are environment caveats, not code failures.
  - S1 closes PASS; S2 is READY. No S2 implementation was started.

## Evidence index

- Contract decisions: `docs/PRODUCT_CONTRACT.md`.
- Architecture and boundaries: `ARCHITECTURE.md`.
- UX/import/export contract: `docs/UX_EXCEL_SPEC.md`.
- Legacy reference baseline: `/opt/Mobiup/grile-salarii` commit
  `c131c21cb29131d0dfa0d6b986a0831e19549d5f` (read-only observation).
- Retail protected baseline: `/opt/Mobiup/unihub-retail` commit
  `6a3e71484e3f2e399c2fc6db42a98ab1ba8c0251` (read-only observation).
- S1 attempt-2 builder evidence: `.agent/evidence/UGR-001/S1/` — README index
  plus `health-probe.txt`, `schema-evidence.txt`, `ingest-fixture.txt`,
  `ac02-conflict-probe.txt`, `coverage-report.txt`, `worker-probe.txt`,
  `compose-stack-proof.txt`, `concurrent-ac02-pg.txt`, `test-summary.txt`,
  `typecheck-summary.txt`. All sanitised; no credentials, no production rows.
- S1 decisions: `docs/decisions/s1-stack.md`.
- S1 local commands: `docs/operations/local-commands.md`.
- S1 developer stack: `docker-compose.yml` (postgres + api + worker + web).
- S1 primary checks on the candidate commit: backend pytest `36 passed`
  (33 SQLite + 3 PostgreSQL integration), ruff PASS, mypy PASS on 35 source
  files, frontend tsc PASS, Vite build PASS; PostgreSQL chain lands at
  `b9fbb01f8cd0`; API assignment probe `200/409/409`; concurrent
  `uq_site_day_one_working` / `uq_person_day_one_working` race rejects one of
  two threads; Compose `/api/healthz` + `/api/ingest/fixture` work through
  the Vite dev proxy; durable worker started via `python -m ugrile.worker.worker`
  drains the outbox to `DONE`.
- S1 attempt-1 failure probes (resolved): `python -m ugrile.worker.worker`
  exited `0` in ~1s with no executable entrypoint — fixed by adding
  `if __name__ == "__main__": main()`; isolated two-tenant fixture probe
  retained Store/Person rows only under the first tenant — fixed by
  tenant-encoded synthetic IDs plus composite `(tenant_id, target_id)` FKs.

## Integration, regression, and deployment

- Integrated diff: exact candidate `153d283d6eacf47b354a07983b7b12cc0a8c4101`
  ahead of `planning-baseline-20260820`; see
  `.agent/evidence/UGR-001/S1/README.md` for the live verification steps.
- Global checks on candidate `153d283d6eacf47b354a07983b7b12cc0a8c4101`:
  backend `pytest` 36 passed (33 SQLite + 3 real PostgreSQL integration),
  frontend `vitest` 2 passed, `ruff check`, `mypy --strict`, `tsc --noEmit`,
  and Vite build all passed in the captured handoff evidence.
- Published artifact/SHA: NOT IN SCOPE.
- Runtime health/logs/user flow: `/healthz`, `/readyz`, and `/version` return
  `ok` with Alembic head `b9fbb01f8cd0`; the dedicated
  `python -m ugrile.worker.worker` process drains typed NOOP/FIXTURE_INGEST jobs;
  the fixture ingest applies 2 stores + 3 people + 3 sales + 2 targets
  idempotently under the requested tenant. Fresh Compose replay in this
  sandbox was limited by external Docker DNS/apt resolution, while the
  committed Compose routing is statically verified and the developer-host
  proof is captured in `.agent/evidence/UGR-001/S1/compose-stack-proof.txt`.

## Risks and remaining work

- Formula salarială V2 completă, ore/pauză, sărbători și close authority trebuie
  confirmate înainte de S3/S5.
- Google anonymous protection must be proven on a copied canary; documentation is
  not proof.
- The one-agent/store/day assumption is a hard business invariant. If reality
  changes, attribution logic and contract must be revised before use.
- Cheap builders may optimize locally and violate boundaries; every handoff must
  be checked against exact acceptance criteria, not against their summary.
- Retail may drift substantially before S7; re-baseline rather than port old code
  blindly.

## Next exact step

S1 is GO/PASS on exact candidate
`153d283d6eacf47b354a07983b7b12cc0a8c4101`; S2 is now READY. Return S2 to one
builder and implement only the S2 contract:

1. implement revisioned calendar use cases and manager scope;
2. derive person calendar, store coverage and Pontaj from one authority;
3. implement XLSX schedule template, preview, validation and atomic CAS apply;
4. prove mid-month edits update Pontaj in the same revision;
5. keep hours/pause configurable and document any still-unconfirmed value.

The S2 builder must stop after one coherent commit and complete handoff. Do not
start S3; formula, hours/pause, holidays and close-authority confirmations remain
required before S3.

## Resume procedure

1. Read `AGENTS.md`, `README.md`, `ARCHITECTURE.md`, product/UX contracts, and
   this plan.
2. Run `git status --short --branch`, `git log -5 --oneline --decorate`, and
   compare the exact HEAD with the latest progress entry.
3. Preserve all newer work. Do not reset or rewrite another builder's commit.
4. Identify the single stage in `READY` or `BUILDING`; do not start a later one.
5. Continue from `Next exact step` and update this plan after material progress.
