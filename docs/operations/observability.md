# Observability and safe logging

Status: server-test candidate contract  
Tracker: issue #4 (`OPS-001`)

UniHub Grile uses JSON structured logs for operational diagnosis. Logs are a
**metadata channel**, not a business-data export. The implementation is
fail-closed: only an explicit scalar allowlist may reach stdout.

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
code.

The concrete URL, path parameters and query string are deliberately not logged.
An unmatched route is recorded as `route=unmatched` rather than echoing the
requested path.

## Worker events

Worker retry/failure events may contain only operational queue metadata such as:

- correlation ID from the originating request;
- job kind/id;
- attempts/max attempts;
- retry delay and retryability;
- lease/status metadata;
- exception **type** and safe typed code.

Arbitrary exception messages are not emitted to stdout. Richer failure text may
remain in the existing durable `last_error`/provider diagnostic state, where API
scope/redaction and operator workflows already govern access.

## Explicitly forbidden in stdout logs

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

`core/logging.py` owns the stdout allowlist. Unknown keys are dropped rather than
heuristically scrubbed. Only bounded strings, numbers, booleans and `null` are
retained. Collections/objects are discarded even if attached to an allowlisted
key.

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
  "log_level": "info",
  "timestamp": "2026-08-23T18:00:00Z"
}
```

No tenant/person/store identifiers or financial values are required to diagnose
basic latency/error/queue flow.

## Operator lookup

When troubleshooting:

1. capture the response `X-Correlation-ID` from the failing interaction;
2. search API structured logs by that ID;
3. if the action enqueued work, search worker logs by the same ID;
4. use the scoped Joburi/API diagnostic surface for controlled failure detail;
5. use audit records for business-mutation evidence rather than stdout logs.

Do not solve missing observability by temporarily logging raw payloads in a
server-test or production-capable environment.
