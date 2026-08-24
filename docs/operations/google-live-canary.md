# Bounded Google live-canary procedure — GS-010

Status: **procedure only**. This document does not itself authorize any Google request, deployment, production mutation, or live canary.

The live canary may be executed only during an explicitly opened server-test window with separate human authorization that names the exact non-production target. Normal CI, local development, PR review, and tracker completion for GS-010 perform **zero external Google requests**.

## Purpose

The canary proves that the already-tested live transport behaves correctly against one real Google Sheet without turning server test into an open-ended provider exercise. It validates the narrow contracts established by GS-001..GS-009:

- stable store-to-Sheet binding;
- bounded live projection and read-after-write reconciliation;
- exact E-pay input block/readback;
- managed Sheet protection;
- last-good/failure behavior and operational metadata.

It is not a substitute for deterministic CI and it is not a production readiness or deployment authorization.

## Hard bounds

One authorized canary run is limited to:

- **one** standalone test tenant;
- **one** OPEN test month;
- **one** test store;
- **one** dedicated non-production spreadsheet with bound `Grila` and `Pontaj` tabs;
- at most **two** live projection executions: initial publication plus one refresh used only to prove E-pay input preservation;
- at most **one** Google E-pay readback/persistence call after controlled H/I edits;
- no bulk/all-store projection;
- no close/reopen operation;
- no production tenant, store, spreadsheet, or real payroll/E-pay data;
- immediate stop on the first unexpected mutation, mismatch, protection conflict, stale identity, or provider error.

A new attempt after a stop is a new canary run and requires a fresh authorization/evidence record. Do not repeatedly retry a failing live target.

## Required authorization record

Before enabling any live mutation, record all of the following in the private server-test evidence log:

1. explicit authorization to execute this canary;
2. executor and change window;
3. exact deployed application commit SHA;
4. test tenant id, month id and store id;
5. human-readable test spreadsheet label plus a SHA-256 fingerprint of the spreadsheet id;
6. bound Grila/Pontaj tab names;
7. planned idempotency keys for the initial projection and optional refresh;
8. confirmation that the target is non-production and contains no real payroll/customer data.

Do **not** put credential JSON, access tokens, service-account private keys, or the credential file contents in tracker comments, logs, screenshots, shell history or evidence artifacts.

The spreadsheet id itself is operational binding data, not a credential, but the public tracker should use the fingerprint rather than the raw id.

## Target preparation

Use a disposable Sheet created specifically for server testing. It must not be a copy currently used by a real store.

Before the canary:

- create/confirm the `Grila` and `Pontaj` tabs named exactly as the binding expects;
- ensure the service account has only the access required for the canary;
- bind the test store explicitly through the existing binding workflow; never discover or rebind by spreadsheet name;
- keep an unrelated sentinel tab/value in the workbook if practical so the post-check can prove non-owned content was untouched;
- use synthetic people/program/E-pay values only;
- confirm the month is OPEN;
- confirm no stale projection job for the same target is RUNNING/PENDING.

Binding creation/rebind is a separate administrative action. If it is required for the server-test target, its authorization and reason must be recorded separately; projection must never create or redirect a binding implicitly.

## Configuration gates

All existing live gates remain mandatory:

```text
UGRILE_GOOGLE_PROVIDER=live
UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED=true
UGRILE_GOOGLE_CREDENTIALS_FILE=/externally/mounted/path/service-account.json
```

The credential path must point to the externally mounted regular file defined by `google-provider-config.md`. Never place credential material in the repository or inline environment text.

Enable the mutation gate only for the bounded canary window. After the run, set `UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED=false` (or return the service to the `fake` provider) before any unrelated work continues.

## Preflight — no Google mutation yet

Before the first projection:

1. verify the running server reports/uses the exact authorized application SHA;
2. read the existing store binding and compare the complete identity: spreadsheet id + Grila tab + Pontaj tab;
3. confirm local month/store authorization and the current calendar-data revision;
4. record the current local last-good projection generation/reconciliation, if one exists;
5. confirm the target workbook contains only test data and record the sentinel/unowned content state;
6. confirm no credential contents appear in application logs;
7. confirm the planned operation is still within the one-store/two-projection/one-E-pay-readback bound.

`GET /months/{month_id}/canary/readback?store_id=...` is a useful persisted structural check, but it is **not** evidence that Google was contacted. The live proof comes from the exact live projection reconciliation described below.

## Canary execution

### 1. Initial live projection — exactly once

Enqueue one store projection through the normal durable API path:

```http
POST /months/{month_id}/sheet-projection/enqueue
Content-Type: application/json

{
  "store_id": "<test-store-id>",
  "idempotency_key": "gs010-canary-initial-<run-id>"
}
```

Use the normal authorized server-test identity headers/session for the test tenant. Do not call provider internals directly and do not hand-create an outbox row.

Wait for that exact job id to reach terminal state through the existing job diagnostics. Do not enqueue another projection while it is RUNNING/PENDING.

**Required result: DONE.** Any FAILED/retry state is a stop condition for this canary; capture the typed diagnostic and disable live mutations rather than repeatedly retrying manually.

### 2. Prove live reconciliation

Read:

```http
GET /months/{month_id}/sheet-reconciliation?store_id=<test-store-id>
```

Require all of the following:

- `available=true`;
- `verified=true`;
- `verification_mode=live_readback`;
- `format_version=v2`;
- store/month/revision match the authorized target/current data revision;
- rule-pack version is present;
- projection checksum is present;
- the returned generation matches the successful projection.

A local DONE job without `verified=true` live readback is not a passing canary.

### 3. Prove protection boundary

Inspect the dedicated test workbook:

- `Grila!A:E` is not operator-editable;
- `Grila!G` identity/control cells are not operator-editable;
- only H:I for the exact current expected person rows are operator-editable;
- all other Grila cells remain protected;
- `Pontaj` is fully read-only to the operator;
- service-account managed protection is present; its editors are exactly the
  service account plus the Google-reported file owner identities;
- unrelated/sentinel workbook content and non-conflicting external protection remain unchanged.

If an unexpected cell is editable, an expected H/I cell is blocked, or an unrelated protection/content item changed, stop immediately.

### 4. Controlled E-pay edit and one readback

Enter synthetic values `0..10` only in the permitted H/I cells. Use values that make accidental row/category swapping obvious (for example distinct values for each category/person).

Execute exactly one remote readback through:

```http
POST /months/{month_id}/epay/google-readback?store_id=<test-store-id>
```

Require:

- exact expected person set;
- exactly `UNDER_50` and `AT_OR_OVER_50` for each expected person;
- values equal the controlled H/I inputs;
- no extra/missing/duplicate observations;
- resulting E-pay freshness for the test store/month is valid according to the existing contract.

Do not use a CLOSED month and do not close/reopen the month as part of the canary.

### 5. One optional refresh to prove E-pay preservation

This is the **second and final** allowed projection for the canary. Use a new explicit idempotency key so the durable API creates a distinct same-revision projection execution rather than replaying the initial idempotent job:

```http
POST /months/{month_id}/sheet-projection/enqueue
Content-Type: application/json

{
  "store_id": "<test-store-id>",
  "idempotency_key": "gs010-canary-refresh-<run-id>"
}
```

Require the refresh job to reach DONE and require live reconciliation to be verified again. Then confirm that the controlled H/I values survived unchanged and that the protection boundary still matches exactly.

If E-pay preservation is not part of the authorized canary objective, skip this step; do not spend the second projection merely for repetition.

## PASS criteria

The canary is PASS only if every executed step satisfies its exact assertions and all of these remain true:

- no request touched a production tenant/store/spreadsheet;
- no operation exceeded the hard bounds;
- all live projection jobs executed in the run are DONE;
- latest reconciliation is verified `live_readback` for the exact target revision;
- managed Grila/Pontaj values read back exactly;
- editability is limited to expected E-pay H/I cells;
- E-pay Google readback matches the controlled values exactly when that step was executed;
- optional refresh preserves those E-pay values;
- unrelated/sentinel content remains unchanged;
- no credential material appears in evidence/logs;
- local last-good state was not destroyed by any failure.

## STOP / FAIL criteria

Stop and mark the run FAIL on the first:

- binding identity mismatch or stale pin;
- unexpected target/revision/generation;
- provider transport/quota/auth error;
- `GOOGLE_LIVE_READBACK_MISMATCH` or other reconciliation mismatch;
- protection conflict or incorrect editable range;
- missing/extra/duplicate/misaligned E-pay row;
- unexpected mutation outside owned ranges/protections;
- job that does not reach the expected terminal state;
- evidence that the target is production or contains real payroll/customer data;
- credential/token material appearing in logs/evidence.

A FAIL must not be converted to PASS by rerunning until one attempt happens to work. Diagnose first; a subsequent attempt is a separately authorized canary run.

## Shutdown / recovery

Immediately after PASS or FAIL:

1. disable `UGRILE_GOOGLE_LIVE_MUTATIONS_ENABLED` or return provider mode to `fake`;
2. confirm no canary job remains RUNNING/PENDING;
3. preserve typed diagnostics and exact job/generation/checksum evidence;
4. leave the disposable test Sheet isolated from production use;
5. if a test binding is to be removed/rebound, perform that as a separately authorized binding operation with the normal CAS/reason/audit contract;
6. do not delete projection/audit evidence merely to clean up the test.

No deploy, release, tag, production promotion, close/reopen, or Retail mutation follows automatically from a passing canary.

## Evidence template

Record, without secrets:

```text
CANARY RESULT: PASS | FAIL
application_sha: <exact deployed SHA>
authorization_ref: <private change/test authorization>
tenant_id: <test tenant>
month_id: <test month>
store_id: <test store>
spreadsheet_fingerprint_sha256: <sha256 of spreadsheet id>
grila_tab: <name>
pontaj_tab: <name>
initial_job_id: <id>
initial_generation: <generation>
initial_projection_checksum: <sha256>
initial_reconciliation_verified: true|false
epay_readback_executed: true|false
epay_exact_match: true|false|n/a
refresh_job_id: <id|n/a>
refresh_generation: <generation|n/a>
refresh_projection_checksum: <sha256|n/a>
epay_preserved_after_refresh: true|false|n/a
protection_contract_verified: true|false
sentinel_unowned_content_unchanged: true|false
live_mutation_gate_disabled_after_run: true|false
unexpected_mutations: <none or exact description>
typed_errors: <none or non-secret codes>
```

A tracker comment may reference the private evidence record and exact application SHA, but must not copy credentials or other secret material.

## Repository/CI boundary

GS-010 is complete when this bounded procedure is reviewed, tested as a documentation safety contract, passes required exact-head CI, is SHA-guard merged, and has tracker evidence. **Executing the live canary is intentionally not a GS-010 completion requirement.** It belongs to the separately authorized server-test phase.

`unihub-retail` remains read-only throughout this procedure. A live-canary authorization for UniHub Grile never authorizes a Retail mutation.
