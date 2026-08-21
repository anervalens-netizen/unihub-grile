# UniHub Grile — quality gates

Status: canonical verification contract for issue #3 / tracker #4.

## 1. Purpose

The project target is not “all tests green.” The target is a **standalone plugin
candidate >=8.5/10 that is safe to move to server testing**.

A claim must be supported by evidence appropriate to that claim. Earlier stage
PASS results remain historical evidence for exact commits/contracts, but the
final gate is evaluated again on the exact candidate commit.

## 2. Score model

Each area is scored 0–10:

| Area | Candidate target | Hard minimum |
|---|---:|---:|
| Domain / payroll correctness | 9.0 | 8.5 |
| Calendar / pontaj / attribution | 9.0 | 8.5 |
| Backend/API | 8.5 | 8.0 |
| Authorization / scope safety | 9.0 | 8.5 |
| Frontend/UX | 8.5 | 8.0 |
| Google/XLSX | 8.5 | 8.0 |
| Worker/runtime resilience | 8.5 | 8.0 |
| Tests/CI | 9.0 | 8.5 |
| Observability/operations | 8.0 | 7.5 |
| Retail integration preparedness | 9.0 | 8.5 |

Overall score must be **>=8.5/10** and no hard minimum may be missed.

A score cannot compensate for a P0 correctness/scope/data-loss/financial-close
defect.

## 3. Evidence matrix

| Claim | Minimum evidence |
|---|---|
| Pure calculation rule | unit + table/golden edge tests |
| Determinism/hash | repeated identical canonical input -> identical output/hash |
| DB invariant | PostgreSQL constraint/repository test |
| Concurrency/CAS | real PostgreSQL concurrent probe/test |
| Resource authorization | positive + cross-manager + cross-tenant negative tests |
| Closed-month immutability | tests across every write route/domain path |
| Audit | persisted event with actor/before/after/revision + chain/integrity test where used |
| Worker retry/recovery | crash/stale/timeout/duplicate scenarios |
| Frontend behavior | component test; browser/runtime proof for real interaction/visual claims |
| Responsive/visual quality | browser at target viewports |
| XLSX | parse produced workbook and inspect required cells/sheets/links |
| Google projection | fake provider structural tests; bounded live canary only at approved gate |
| Performance | measured representative fixture and recorded methodology |
| Deployment/readiness | actual runtime probe on target-like environment |
| Retail compatibility | contract/adapter tests inside Grile; real Retail integration only in later program |

Unavailable evidence must be recorded as unavailable. It does not become PASS by
reasoning alone.

## 4. P0 / P1 blocking classes

### P0
Any issue that can:
- expose one manager/tenant's protected data to another;
- calculate or close materially incorrect payroll without blocker;
- lose/corrupt business truth;
- allow closed-month mutation outside approved reopen;
- duplicate/erase accepted financial/source data;
- make a dangerous development endpoint available in production unintentionally.

No candidate gate may pass with an open P0.

### P1
Examples:
- worker can strand required jobs indefinitely;
- meaningful audit gap on business mutation;
- unreliable generation/reconciliation handling;
- primary operational flow unusable or misleading;
- mandatory CI not covering a critical layer;
- severe performance issue at expected scale.

No server-test-ready declaration with an unresolved P1 affecting correctness,
scope, data loss or financial close. Other P1s require explicit user acceptance,
not silent downgrade.

## 5. Mandatory domain/financial gate

Must prove on exact candidate:
- all Mobiup golden threshold cases;
- `EXTRA_HOME` and `EXTRA_OTHER` semantics;
- no physical sales duplication on reassignment;
- salary/tickets/Flip/incentive/SIM/E-pay inputs traceable;
- `Decimal`/rounding contract;
- missing required inputs produce anomaly/blocker as designed;
- E-pay required/fresh for final close;
- canonical inputs round-trip to stored hash;
- exact rule-pack version/hash persisted.

## 6. Calendar/pontaj/attribution gate

Must prove:
- max one working person per store/date;
- max one working store per person/date;
- valid home/other rules;
- stale revision rejected without lost updates;
- mid-month change updates pontaj/attribution/grid coherently;
- OFF/LEAVE scope semantics correct;
- store/company physical totals remain unchanged by personal reassignment.

## 7. Authorization gate

A machine-readable or documented endpoint matrix must exist and tests cover:
- admin tenant-wide allowed cases;
- manager allowed scope;
- manager forbidden store/person;
- tenant boundary;
- jobs/export/download/status resources;
- E-pay/Sheet resources;
- close/reopen capabilities;
- frontend control visibility consistent with backend capabilities.

Security is backend-enforced. Hidden buttons alone are not evidence.

## 8. Close/reopen gate

Close must:
- lock/revalidate revision/state;
- evaluate versioned blocker policy;
- have zero blocking conditions;
- validate required financial inputs/generations;
- persist final snapshot/digest/audit;
- reject concurrent/stale close correctly.

Reopen must:
- require authorized admin capability;
- require reason;
- preserve previous close history;
- increment/transition state coherently;
- produce audit.

## 9. Worker/runtime gate

Must prove:
- `run_after` honored;
- stale RUNNING jobs recover;
- retry bounded by type;
- retryable vs terminal failure separated;
- duplicate enqueue/idempotency controlled;
- obsolete revision jobs safely superseded where applicable;
- last-good projection/export state preserved;
- operator can identify queue/running/retry/failed/done state.

## 10. Backend/performance gate

Representative fixture: target equivalent to at least 75 stores and 150 people.

Targets:
- no unbounded N+1 on primary screens;
- representative overview p95 <500 ms local/pilot target;
- normal calendar DB save path <500 ms local target;
- calendar load <1 s target where environment permits;
- query count/latency evidence stored in PR/issue;
- fresh PostgreSQL migration/bootstrap PASS.

If host variance prevents a strict time target, record hardware/environment and
compare repeatably; do not fabricate compliance.

## 11. Frontend gate

Must cover:
- Hub;
- Program edit + 409 recovery;
- store detail;
- Excepții;
- grid/pontaj/E-pay state;
- Management close/reopen;
- async job/sync/export state where exposed;
- loading/empty/stale/403/409/error;
- subsystem isolation;
- capability-aware controls;
- responsive desktop/tablet/mobile;
- keyboard accessibility.

Build + component tests are mandatory but insufficient for visual/runtime PASS.
Browser evidence is required for the final visual/interactivity score once a
browser runner is available.

## 12. Google/XLSX gate

Google fake/provider tests:
- binding lifecycle;
- projection structure;
- last-good preservation;
- structural readback mismatch;
- E-pay exact expected set;
- invalid values preserve last-good;
- protection contract;
- retry/error classification.

Live Google:
- only bounded approved canary;
- no production registry/live file mutation before explicit server-test gate;
- evidence sanitized: no sheet IDs/credentials committed.

XLSX:
- parse per-store workbook;
- validate `Grila` + `Pontaj` structure;
- validate scoped bulk manifest/checksums;
- validate pontaj-only;
- no external links;
- deterministic naming/output metadata;
- artifact retention policy.

## 13. CI gate

Exact candidate commit must have mandatory checks green:
- backend ruff/lint;
- backend strict mypy;
- backend unit/domain/API tests;
- PostgreSQL integration/concurrency suite;
- migration bootstrap;
- frontend TypeScript/build;
- frontend component tests;
- cross-stack suite where defined;
- browser E2E when runner support is part of final gate.

CI is not the preferred iterative debugger; targeted checks should run before
pushing when environment permits.

## 14. Observability/operations gate

Before server testing:
- structured logs;
- correlation/request IDs;
- no known PII/payroll leakage in logs;
- health/liveness/readiness documented and tested;
- job queue/retry/failure observable;
- projection freshness/close blockers observable;
- unsafe prod config rejected;
- environment variable inventory;
- backup/restore/migration runbook;
- worker recovery/remediation runbook;
- server-test startup/rollback or roll-forward procedure.

## 15. Retail integration-preparedness gate

Without modifying Retail, prove:
- versioned identity/data contracts exist;
- FixtureRetailAdapter implements them;
- contract tests are source-agnostic;
- domain/services have no Retail dependency;
- unsupported versions fail closed;
- atomic generation/last-good semantics;
- mapping to current Retail concepts documented from read-only inspection;
- future shell/session/deep-link contract documented;
- expected Retail-side changes are bounded and explicit.

This gate does **not** claim the real Retail integration has been tested.

## 16. Shadow reconciliation gate

Use a representative anonymized cohort equivalent to at least 8 stores and
multiple historical periods where trustworthy source results exist.

For every mismatch, record:
- store/person/period anonymized reference;
- component;
- old result;
- new result;
- delta;
- root cause;
- disposition: legacy defect / intentional contract change / rounding/version /
  Grile defect / source uncertainty.

There must be **zero unexplained payroll-impacting mismatches** before candidate
approval.

## 17. Final adversarial audit

On exact candidate commit, perform a fresh full-stack audit covering:
- architecture boundaries;
- backend/domain;
- auth/scope;
- concurrency;
- worker recovery;
- frontend behavior;
- Google/XLSX;
- CI/operations;
- documentation consistency;
- Retail integration preparedness.

The audit score table and unresolved findings are appended to issue #4.

## 18. SERVER-TEST-READY

Issue #4 checkbox `SERVER-TEST-READY` may be checked only when:

- overall >=8.5/10;
- every hard-minimum area passes;
- no open P0;
- no unresolved P1 affecting correctness/scope/data loss/financial close;
- mandatory CI green on exact candidate SHA;
- zero unexplained payroll-impacting reconciliation mismatch;
- primary frontend workflows have appropriate runtime/browser proof;
- server-test runbook/environment inventory complete;
- Retail repository remains unmodified by the current program.

This gate authorizes *testing the standalone candidate on a server*. It does not
automatically authorize production deployment or modification of UniHub Retail.
