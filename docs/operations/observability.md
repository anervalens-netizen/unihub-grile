# Observability, health and safe logging

Status: server-test candidate contract  
Tracker: issue #4 (`OPS-001`, `OPS-002`, `OPS-003`)

UniHub Grile uses JSON structured application events for operational diagnosis.
Those events are a **metadata channel**, not a business-data export. The
application event pipeline is fail-closed: only an explicit scalar allowlist may
reach its JSON output.

## Correlation lifecycle

1. An API request may provide `X-Correlation-ID`.
2. Only bounded IDs matching the Grile correlation grammar are accepted;
   otherwise the API generates `req_<uuid>`.
3. The ID is bound to request-local `contextvars` and returned in the response
   header.
4. Durable job enqueue copies the current correlation ID into job diagnostic
   metadata.
5. Worker execution restores that ID into the structured logging context before
   the handler and settlement path run.
6. Audit/business records that already persist correlation keep their existing
   governed representation; logging does not replace audit evidence.

This permits an operator to follow an API action into asynchronous processing
without logging the request body or payroll data.

## Request events

The API emits one structured completion event per handled request:

- `event=http_request_completed`
- `correlation_id`
- HTTP `method`
- matched **route template** such as `/months/{month_id}/program/cell`
- `status_code`
- `duration_ms`

Unhandled middleware failures use `http_request_failed` with the same safe
metadata plus an exception class and, when available, a conservative typed error
code. The client receives the canonical generic `INTERNAL_ERROR` envelope and
the correlation header; the arbitrary exception message is neither reflected to
the client nor re-thrown into Uvicorn's traceback logger.

The concrete URL, path parameters and query string are deliberately not logged.
An unmatched route is recorded as `route=unmatched` rather than echoing the
requested path. Uvicorn's default access log is disabled in the programmatic
entrypoint so it cannot bypass this contract by emitting raw request targets.

## Worker events

Worker retry/failure events may contain only operational queue metadata such as:

- correlation ID from the originating request;
- job kind/id;
- attempts/max attempts;
- retry delay and retryability;
- lease/status metadata;
- exception **type** and safe typed code.

Arbitrary exception messages are not emitted by Grile worker structured events.
Richer failure text may remain in the existing durable `last_error`/provider
diagnostic state, where API scope/redaction and operator workflows already
govern access.

## Health and readiness

The probe endpoints have intentionally different meanings:

| Endpoint | HTTP contract | Meaning |
| --- | --- | --- |
| `/livez` | 200 while process can answer | Process/event-loop liveness only. It performs no DB/provider check, so a DB outage does not create a restart loop. |
| `/healthz` | 200 while process can answer | Human/operator dependency summary. `status=degraded` reports DB/schema/worker-lease problems without making this a traffic gate. |
| `/readyz` | 200 ready / 503 not ready | Traffic gate. DB must respond, the DB Alembic revision must equal the **current shipped migration head**, and when the worker is enabled there must be no expired RUNNING lease awaiting recovery. |
| `/metrics` | 200 Prometheus text | Privacy-safe operational metrics; DB-backed gauges degrade to `ugrile_metrics_database_up 0` if they cannot be read. |

The expected schema version is derived at runtime from the packaged Alembic
migration scripts. It is not a manually maintained SHA/revision constant.

`/readyz` does **not** pretend to prove that a separate worker process is alive;
the current architecture has no worker heartbeat table. Instead it checks the
state that matters to API traffic safety: whether enabled-worker durable leases
have already expired. Queue state is observable through `/metrics` and the
scoped Joburi diagnostics. A dedicated worker-service supervisor remains
responsible for process liveness.

## Metrics contract

`/metrics` uses Prometheus text exposition without adding tenant/store/person or
financial labels.

Current metrics include:

- `ugrile_http_requests_total{method,route,status_class}`;
- `ugrile_http_request_duration_seconds_sum{method,route,status_class}`;
- `ugrile_jobs_current{status}` for `PENDING/RUNNING/FAILED/DONE`;
- `ugrile_job_retry_backlog` for PENDING jobs that already attempted execution;
- `ugrile_sheet_projection_failures_current`;
- `ugrile_sheet_projection_last_success_age_seconds` (`-1` when no success exists);
- `ugrile_close_blockers_last_observed` and
  `ugrile_close_blocker_observations_total`;
- `ugrile_metrics_database_up`.

HTTP counters/latency are process-local and therefore reset on process restart,
which is normal for process metrics. Queue/projection gauges are recomputed from
durable DB state on scrape. The close-blocker gauge is deliberately the last
observed authenticated close evaluation and is **unlabeled**; the authenticated
checklist remains the source for entity-level blocker detail.

Do not add correlation IDs, tenant IDs, store/person IDs, spreadsheet IDs,
provider IDs or payroll values as metric labels. Route labels must remain matched
route templates so cardinality stays bounded and identifiers cannot leak.

## Explicitly forbidden in Grile application events

Do not log any of the following, even at debug level:

- request or response bodies;
- query strings or concrete entity-bearing URLs;
- `Authorization`, cookies, host session tokens or Google credentials;
- emails, names or free-form person/store payloads;
- salaries, incentives, E-pay values, grid/payroll amounts or source rows;
- raw Google/Retail provider payloads/responses;
- job payload JSON;
- arbitrary exception messages, `repr(exception)` or exception-local objects;
- API error `details` objects when they can contain business data.

If a new operational field is required, add it to the safe logging allowlist and
add a regression proving it cannot carry a structured payload or PII by
accident.

## Safe-field policy

`core/logging.py` owns the Grile application-event allowlist. Unknown keys are
dropped rather than heuristically scrubbed. Only bounded strings, numbers,
booleans and `null` are retained. Collections/objects are discarded even if
attached to an allowlisted key.

This is intentional: a future developer writing
`log.info("x", payload=request.json())` must not create a data leak merely
because the log call exists.

## Synthetic example

```json
{
  "event": "http_request_completed",
  "correlation_id": "req_7f5c...",
  "method": "POST",
  "route": "/months/{month_id}/program/cell",
  "status_code": 200,
  "duration_ms": 34.2,
  "level": "info",
  "timestamp": "2026-08-23T18:00:00Z"
}
```

No tenant/person/store identifiers or financial values are required to diagnose
basic latency/error/queue flow.

## Operator lookup

When troubleshooting:

1. check `/livez`; if it is down, diagnose/supervise the API process;
2. check `/readyz`; a 503 response identifies DB/schema/stale-lease readiness state;
3. inspect `/metrics` for request errors/latency, queue state and projection freshness;
4. capture the response `X-Correlation-ID` from the failing interaction;
5. search API structured logs by that ID;
6. if the action enqueued work, search worker logs by the same ID;
7. use the scoped Joburi/API diagnostic surface for controlled failure detail;
8. use audit records for business-mutation evidence rather than application logs.

Do not solve missing observability by temporarily logging raw payloads in a
server-test or production-capable environment.
