# Google provider configuration

This document defines the standalone candidate's Google provider and credential
boundary. It does **not** authorize a live Google mutation.

## Provider modes

`UGRILE_GOOGLE_PROVIDER` accepts exactly:

- `fake` — default; deterministic local persistence only, no network I/O;
- `live` — reserved for the production-grade transport being completed under
  GS-001.

`GoogleProjectionService` depends on the `GoogleProjectionProvider` boundary.
The current fake implementation delegates to the already-proven local adapter.
Selecting `live` never silently falls back to fake behavior.

## Live mutation gates

A live projection write is allowed to become reachable only when all of these
conditions are true:

1. `UGRILE_GOOGLE_PROVIDER=live`;
2. `UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED=true`;
3. `UGRILE_GOOGLE_CREDENTIALS_FILE` points to an externally mounted regular
   file;
4. a real live transport implementation exists.

The current repository intentionally fails closed at condition 4. This is
expected until GS-001 lands the real transport. CI and local fixture workflows
must remain on `fake`.

## Credential contract

Google credential JSON/token material must never be:

- committed to this repository;
- pasted into `.env` or `.env.example`;
- stored in job payloads, audit payloads or API responses;
- written to application logs or test evidence.

Only a file-system path may be configured through
`UGRILE_GOOGLE_CREDENTIALS_FILE`. The provider-selection layer checks that the
path exists but does not open or log credential contents. Server deployment must
mount the file from the platform's secret mechanism outside the repository.

The repository already ignores `.env`, `config/google/`, `credentials*.json`,
`token*.json` and `service-account*.json`.

## Fake-provider fault injection

`UGR_S5_GOOGLE_FAIL=1` is retained only for deterministic fake-provider
resilience tests. It is not a provider selector and has no authority to enable a
live mutation.

## Live canary boundary

No live canary is authorized in the current milestone batch. The bounded canary
procedure belongs to GS-010 and server-test readiness. Until then, successful
Google evidence comes from fake-provider structural/readback tests only.
