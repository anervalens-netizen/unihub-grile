# Final adversarial standalone-candidate audit

Status: `VAL-012` / `VAL-013` final gate evidence.

Canonical plan: GitHub issue #3.  
Canonical status/evidence ledger: GitHub issue #4.

## Artifact identity and audit boundary

The functional application baseline inspected by this audit is:

`6b080f1ce707cca0871477f1b17b50fcf65d0da8`

That commit is the verified merge of PR #67 and already contains the complete
M0-M7 implementation plus M8 validation through `VAL-011`.

The final candidate SHA is **not embedded in this document**, because committing
a document that names its own SHA would change that SHA. The final authoritative
candidate identity is recorded in issue #4 after this audit/runbook-only PR has
an exact head, all mandatory CI is green on that head, and the PR is SHA-guard
merged. Server test must check out that recorded exact candidate SHA, not a moving
branch name.

The only permitted delta between the functional baseline above and the final
audit candidate is final-gate documentation/index material. Any production code,
test, migration, workflow, dependency or runtime-config change invalidates this
audit and requires a fresh adversarial review of the changed surface.

## Verdict

**READY FOR FINAL EXACT-HEAD CI GATE**

No open P0 or P1 defect affecting payroll correctness, authorization/resource
scope, data loss, or financial close was found in the exact functional baseline.
The standalone candidate score is **8.9/10**. Every quality area is above its
hard minimum from `docs/QUALITY_GATES.md`.

This verdict means the repository is mature enough to become
`SERVER-TEST-READY` once the final documentation candidate passes mandatory
exact-head CI and its exact SHA is recorded in issue #4. It does **not** authorize
production deployment, a live Google mutation, or modification of UniHub Retail.

## Audit method

The audit was intentionally adversarial rather than a checklist-only review. It
cross-checked current source and canonical docs against the accumulated evidence
chain and looked specifically for false-completion paths:

- architecture/runtime imports and data-authority boundaries;
- auth identity spoofing, cross-tenant/resource IDOR and side effects after deny;
- calendar/Pontaj/attribution revision and scope behavior;
- close/reopen locking, blockers, immutable CLOSED state and audit chain;
- durable worker leases, retry classification, idempotency and supersession;
- Google fake/live boundaries, readback, E-pay exactness and protections;
- deterministic XLSX/ZIP generation and external-link isolation;
- frontend capability rendering, failure isolation and real-browser operation;
- PostgreSQL migrations, query budgets and realistic 80-store/160-person load;
- logging/privacy, health/readiness/metrics and recovery procedures;
- Retail adapter/identity/shell contracts without Retail runtime coupling;
- documentation consistency and server-test safety boundaries.

Current source was also searched for obvious unfinished production paths such as
`TODO`, `NotImplementedError`, placeholder/demo operational behavior and direct
Retail runtime imports. No blocking production-path instance was found.

## Blocking-class review

### P0

No open P0 was found.

In particular, current evidence proves:

- tenant and manager resource isolation across the major route families;
- dev-header tenant spoofing is rejected;
- cross-tenant store/person IDOR is denied before persisted side effects;
- CLOSED financial/business mutation paths fail closed;
- final close revalidates current revision/generation and required financial
  inputs under the versioned policy;
- source-generation missing data is not silently substituted with zero;
- historical reconciliation has zero unexplained payroll-impacting mismatch.

### P1

No unresolved P1 affecting correctness, scope, data loss or financial close was
found.

The remaining limitations listed below are server-test obligations or later
integration work, not hidden P1 defects in the standalone candidate.

## Quality score

| Area | Target | Hard minimum | Score | Adversarial rationale |
| --- | ---: | ---: | ---: | --- |
| Domain / payroll correctness | 9.0 | 8.5 | **9.2** | Versioned deterministic rule pack, Decimal/rounding contracts, close blockers, accepted-generation semantics and M8 historical reconciliation with zero unexplained payroll-impacting mismatch. |
| Calendar / Pontaj / attribution | 9.0 | 8.5 | **9.2** | CAS/revision authority, DB uniqueness invariants, effective-dated scope/home-store semantics, coherent Pontaj/attribution snapshots and real PostgreSQL concurrency coverage. |
| Backend/API | 8.5 | 8.0 | **8.8** | Layer boundaries are strong, error envelope/correlation are standardized, PostgreSQL migrations/drift are gated and primary query counts are bounded. Host-specific write latency still needs server measurement. |
| Authorization / scope safety | 9.0 | 8.5 | **9.3** | Central capability/resource authorization plus route-family re-attestation and final adversarial spoof/IDOR/no-side-effect matrix. Frontend never replaces backend enforcement. |
| Frontend/UX | 8.5 | 8.0 | **8.6** | Primary operator flows, capability-aware controls, independent subsystem states, responsive/accessibility work, component suite and real Chromium E2E exist. Browser coverage is still a bounded operational suite rather than exhaustive visual certification. |
| Google/XLSX | 8.5 | 8.0 | **8.7** | Explicit fake/live seam, dual mutation gates, stable binding, last-good/readback/protection/E-pay contracts and deterministic parser-verified XLSX/ZIP. Real Google live canary is intentionally deferred to separately authorized server test. |
| Worker/runtime resilience | 8.5 | 8.0 | **9.0** | Durable committed leases, stale recovery, bounded retries, DB idempotency, lazy revision supersession, last-good side effects and operator diagnostics are exercised. Worker process liveness relies on the host supervisor rather than an application heartbeat table. |
| Tests/CI | 9.0 | 8.5 | **9.0** | Ruff, strict mypy, PostgreSQL migrations/drift, integration/concurrency, performance, frontend build/tests and Chromium E2E run in CI. Exact-head discipline is strong, but repository branch protection is not the technical enforcement mechanism. |
| Observability / operations | 8.0 | 7.5 | **8.5** | PII/payroll-safe allowlisted structured logs, correlation across API/jobs/audit, readiness/metrics, worker recovery, env inventory and restore-first migration runbook are present. Target-host restore drill/metrics behavior remains to be exercised in server test. |
| Retail integration preparedness | 9.0 | 8.5 | **9.1** | Versioned source and identity contracts, fixture adapter parity, fail-closed schema negotiation, generation semantics, shell/deep-link contract and bounded Retail-side change list exist with no direct runtime dependency. Real Retail identity/data adapters are correctly deferred. |

Arithmetic mean: **8.94**, reported as **8.9/10**.

No area misses its hard minimum. A high score does not waive the blocking-class
rules above.

## Exact evidence snapshot before final-doc PR

The latest functional candidate validation (PR #67, head
`9efa5e5deb51522650ef56f8802a5442f1f5d060`) produced:

- Backend CI `32700684080`: SUCCESS;
  - Ruff PASS;
  - strict mypy PASS over 106 source files;
  - fresh PostgreSQL migration bootstrap PASS;
  - Alembic metadata drift PASS;
  - M3 performance contract PASS;
  - non-performance suite: **519 passed, 2 skipped, 2 deselected**;
- Frontend CI `32700684195`: SUCCESS;
- Browser E2E `32700684073`: SUCCESS;
  - evidence artifact `9510411407`;
  - digest `sha256:b0c6a9f204a1f7423d9a57a07ad3ae2e879d20c985e5b72ee8dc25406494b88b`.

PR #67 merged as
`6b080f1ce707cca0871477f1b17b50fcf65d0da8`, with parent1 the prior exact main
and parent2 the exact certified PR head; GitHub signature verification is valid.

The final documentation PR must produce a new exact-head CI set. The evidence
above is supporting functional evidence, not a substitute for that final run.

## Reconciliation gate

`docs/validation/m8-reconciliation-report.md` records:

- accepted source periods: V1 June/July 2026 month-end plus V2 August pilot;
- V2 grid/payroll cohort: 8 stores / 16 participants;
- threshold agreement: 16/16;
- canonical whole-RON final-total agreement: 16/16;
- independent populated V1 Pontaj evidence: 8/8 qualified stores;
- mismatch ledger: 6/6 explained;
- `Grile defect` entries: 0;
- unexplained payroll-impacting mismatches: **0**.

The audit accepts the source-qualified split because blank V2 Pontaj was
explicitly rejected as an oracle rather than treated as zero evidence.

## Performance interpretation

The performance gate must not confuse shared-runner wall time with structural DB
regression.

The realistic PostgreSQL fixture is 80 stores / 160 people. Current locked query
budgets are bounded for Overview, Program, Grid and the composite Store screen.
The one-cell save path was reduced from an initial pathological **5,025 SQL
statements** to **29 statements / 20 SELECTs** while preserving complete revision
snapshots.

A legacy/local SQLite save target below 500 ms remains covered. Shared GitHub
PostgreSQL runners have shown substantially higher wall times, so server test must
record target-host hardware/environment and measure the same operation there.
This is a server-test performance obligation, not permission to fabricate a
`<500 ms` PostgreSQL claim from CI data.

## Remaining server-test obligations

These are deliberately **not** marked as already proven:

1. **Exact host performance:** record representative read/save latency on the
   intended server-test host with the 80/160-equivalent fixture.
2. **Restore drill:** if server-test state becomes valuable before a risky
   migration/update, prove one isolated restore from a verified backup.
3. **Runtime supervision:** verify the host supervisor actually restarts/alerts
   for API/worker process failure; `/readyz` does not claim a worker heartbeat.
4. **Live Google canary:** remain on fake provider by default. Execute
   `docs/operations/google-live-canary.md` only with a separate explicit
   authorization naming one disposable non-production target.
5. **Production identity:** `APP_ENV=prod` is intentionally blocked until a real
   trusted external/Retail identity adapter exists. Standalone server test uses
   the documented non-production configuration instead.
6. **Real Retail integration:** remains outside this program. No server-test-ready
   decision authorizes a Retail commit, DB write, service change or deployment.

## Findings that were investigated but are not defects

- `GET /version` is present in the FastAPI application. It reports application
  version/environment, not a Git commit. Exact candidate identity therefore must
  be attested from the checked-out artifact/commit before process start and
  recorded externally in issue #4/server-test evidence.
- Production startup rejecting both `dev_headers` and the reserved-but-unmounted
  `external` identity provider is intentional fail-closed behavior. The current
  program targets standalone **server testing**, not production activation.
- A live Google canary is not required to complete M5 or the standalone candidate
  build. It is deliberately bounded and deferred to a separately authorized
  server-test window.

## Final-gate rule

Issue #4 may record `VAL-012`, `VAL-013`, `VAL-014` and `SERVER-TEST-READY` only
when all of the following are true for the final documentation candidate:

- PR head is exact and unchanged;
- mandatory Backend, Frontend and Browser workflows are green on that exact head;
- PR diff contains only the intended final audit/runbook/index material;
- base is the exact expected main and main did not drift before merge;
- merge uses expected-head SHA protection;
- merge topology proves parent2 is the exact certified candidate;
- the tracker records both the **exact tested candidate SHA** and resulting main
  merge SHA;
- no new P0/P1 finding appears during final review;
- UniHub Retail remains unmodified.

Passing this gate authorizes **standalone server testing only**.