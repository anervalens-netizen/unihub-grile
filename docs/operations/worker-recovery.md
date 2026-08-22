# Worker recovery and supersession contract

Canonical status: issue #4 (`JOB-*`). This document describes the standalone Grile worker contract only. It does not authorize production deployment or any UniHub Retail mutation.

## 1. Queue state model

Durable jobs use four persisted states:

- `PENDING` — queued or scheduled for retry; executable only when `run_after <= now`;
- `RUNNING` — claimed with `locked_at` + `locked_by` and a committed lease;
- `DONE` — terminal success;
- `FAILED` — terminal failure, including exhausted retries, deterministic domain failure, or an obsolete revision-bound operation.

`attempts` increments at claim time. Retry limits and deterministic bounded backoff are selected per job kind.

## 2. Crash and stale lease recovery

Claims are committed before handler execution. A process crash therefore leaves visible `RUNNING` state rather than an invisible transaction.

On the next claim cycle, expired `RUNNING` rows are locked and recovered deterministically:

1. below the job-kind attempt limit → return to `PENDING`, clear the lease, become immediately due;
2. at/above the attempt limit → become terminal `FAILED` with `LEASE_EXPIRED_MAX_ATTEMPTS`;
3. a live handler keeps the outbox row locked while executing, so PostgreSQL `SKIP LOCKED` prevents a second worker from stealing an active job merely because wall-clock lease age advanced.

No `RUNNING` row is intended to require an undocumented manual database edit for recovery.

## 3. Retry classification

Retryable failures include provider/transport/infrastructure classes such as:

- Google adapter provider failures;
- `ConnectionError`;
- `TimeoutError`;
- `OSError`;
- SQLAlchemy `OperationalError`.

Retryable failures move back to `PENDING` with bounded backoff until the job-kind attempt budget is exhausted; the final attempt becomes `FAILED` with `RETRY_EXHAUSTED`.

`DomainError` and other deterministic business/payload failures are terminal immediately and are not retried.

Handler-authored diagnostic state is committed with settlement when the transaction remains healthy. If the handler poisons the transaction, the worker rolls it back and settles the outbox row in a fresh transaction.

## 4. Revision-bound supersession

Exports and Google projections pin both:

- administrative `month_revision`;
- calendar-derived data revision.

The canonical supersession strategy is **lazy terminal supersession at execution**, not a separate `CANCELLED`/`SUPERSEDED` queue state.

Before any external/file side effect, the handler locks the in-tenant `Month` row and re-attests both pinned revisions. If either identity advanced, the old job is obsolete and becomes terminal `FAILED` with a typed diagnostic such as:

- `JOB_MONTH_REVISION_STALE`;
- `JOB_DATA_REVISION_STALE`.

This is deliberate cancellation semantics:

- the stale operation performs no projection/artifact publication;
- it is not retried because the request is deterministically obsolete;
- default idempotency identities include revision metadata, so the newer revision receives a different durable job identity;
- a custom idempotency key cannot be silently reused for a different revision/payload; the API fails closed with `409`;
- the operator UI can distinguish the reason from `last_error` while keeping the queue state model small.

Proactive scanning/cancelling of older `PENDING` rows is intentionally not required for correctness. The execution-time Month lock is the authoritative point that also covers jobs already claimed or racing with calendar/close/reopen changes.

## 5. Last-good side effects

Google projection failures append diagnostics without replacing the newest successful projection.

Export attempts publish through an operation-isolated target and atomic temporary-file + `fsync` + `os.replace` sequence. Failed/stale attempts do not mutate the artifact URI/bytes of a prior successful export operation.

## 6. Operator diagnosis

Use the scoped job diagnostics API / `Joburi` UI to inspect:

- queue/running/retry/failed/done state;
- attempts and max attempts;
- `run_after` / lease timestamps;
- typed `last_error` diagnostics;
- persisted month/store resource scope where authorized.

A manager sees only jobs whose persisted resources are provably within effective scope; unscoped jobs fail closed. Admin diagnostics remain tenant-scoped.

## 7. Remediation / run-forward

For a retryable provider outage, normal worker retries are the first recovery path. After `RETRY_EXHAUSTED`:

1. correct/verify the provider or infrastructure condition;
2. inspect the failed job diagnostics and confirm its pinned month/data revision is still current;
3. if the same semantic operation must be attempted again, enqueue a new operator idempotency key; do not mutate the failed row by hand;
4. if the revision advanced, enqueue the normal current-revision operation instead — the old job remains terminal evidence.

For `JOB_*_REVISION_STALE`, do not retry the obsolete row. Run forward by enqueueing the current revision.

For deterministic `TERMINAL` domain/payload errors, correct the source request/data first; blind retry is forbidden.

## 8. Rollback boundary

The worker does not roll business data backward after a failed external side effect. The contract is run-forward plus last-good retention:

- stale jobs fail before publication;
- retried Google work preserves last-good projection;
- export publication is atomic;
- failed job/outbox rows remain durable diagnostic evidence.

Manual deletion or status rewriting of outbox rows is not part of the normal recovery contract.

## 9. Evidence

The M2 evidence suite includes:

- future `run_after` cannot execute early;
- committed crash leases are reclaimed;
- stale leases at max attempts terminate;
- live PostgreSQL row locks cannot be stolen;
- retryable failures back off and exhaust deterministically;
- explicit timeout-class retry/exhaustion proof;
- deterministic domain failures do not retry;
- concurrent duplicate enqueue converges on one durable job;
- revision-advanced export/Google jobs terminate before side effects;
- last-good Google state and prior export artifacts survive failed/stale attempts;
- scoped diagnostics/API/operator UI expose recoverable state without cross-tenant/resource leakage.

Server deployment/runtime recovery still requires the later operations/candidate gates from issue #4; M2 correctness evidence alone is not a deployment approval.
