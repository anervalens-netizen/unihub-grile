# Google provider configuration

This document defines the standalone candidate's Google provider, credential and
live-mutation boundary. It does **not** authorize a live Google canary or any
production Google mutation.

## Provider modes

`UGRILE_GOOGLE_PROVIDER` accepts exactly:

- `fake` — default; deterministic local persistence only, no network I/O;
- `live` — authenticated Google Sheets v4 transport for a store that already has
  an explicit `sheet_bindings` record.

`GoogleProjectionService` depends on the `GoogleProjectionProvider` boundary.
The fake and live implementations share the same deterministic projection input,
but provider selection never silently falls back from `live` to `fake`.

## Live mutation gates

A live projection write becomes reachable only when all of these conditions are
true:

1. `UGRILE_GOOGLE_PROVIDER=live`;
2. `UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED=true`;
3. `UGRILE_GOOGLE_CREDENTIALS_FILE` points to an externally mounted regular
   service-account file;
4. the target store already has a stable `sheet_bindings` row with spreadsheet
   ID plus `Grila` and `Pontaj` tab names.

CI and normal local fixture workflows remain on `fake`. No code path creates a
Google spreadsheet, changes a store binding, or discovers a spreadsheet by name
as a side effect of projection.

## Credential contract

Google credential JSON/token material must never be:

- committed to this repository;
- pasted into `.env` or `.env.example`;
- stored in job payloads, audit payloads or API responses;
- written to application logs or test evidence.

Only a file-system path may be configured through
`UGRILE_GOOGLE_CREDENTIALS_FILE`. The application loads that file only when all
live gates are enabled. Server deployment must mount the file from the platform's
secret mechanism outside the repository.

The repository ignores `.env`, `config/google/`, `credentials*.json`,
`token*.json` and `service-account*.json`.

## Live transport contract

The live provider uses the Google Sheets v4 REST API with the service-account
`spreadsheets` OAuth scope. It performs bounded HTTP calls with a 30-second
per-request timeout by default.

For one store projection it:

1. requires the existing stable binding;
2. reads the currently populated row count of the owned `Grila` (`A:E`) and
   `Pontaj` (`A:G`) ranges;
3. renders deterministic projection values from the revision-bound job payload;
4. pads trailing owned rows with empty values so stale prior projection rows are
   removed;
5. writes both value ranges through one `spreadsheets.values.batchUpdate`
   request using `RAW` input mode;
6. marks the local projection run `DONE` and advances binding generation only
   after the Google write succeeds.

The live writer owns only these value ranges in this batch. Formatting,
protection and E-pay editable-cell rules are separate GS-007/GS-008 work and are
not silently modified here.

## Failure and retry classification

Errors are typed so the durable worker does not retry deterministic operator
problems:

- network/transport failure, HTTP `408`, `429`, and `5xx` → retryable under the
  existing bounded `GOOGLE_PROJECTION_STORE` worker policy;
- credential parsing, missing binding/tab contract, HTTP `400/401/403/404`, or
  invalid projection structure → terminal until remediated.

A failed live write appends diagnostic projection state while preserving the
newest previously successful local projection as last-good. A DB failure after a
remote write may cause the revision-bound job to retry the same deterministic
value publication; it never advances to a newer revision implicitly.

## Fake-provider fault injection

`UGR_S5_GOOGLE_FAIL=1` is retained only for deterministic fake-provider
resilience tests. It is not a provider selector and has no authority to enable a
live mutation.

## Live canary boundary

No live canary is authorized by GS-001/GS-002 implementation work. The bounded
canary procedure belongs to GS-010 and the later server-test phase. Until that
separate gate is explicitly opened, CI evidence for the live transport uses
injected/fake HTTP transports and performs zero external Google requests.
