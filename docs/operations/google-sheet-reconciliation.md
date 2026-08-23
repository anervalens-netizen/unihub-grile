# Google Sheet reconciliation — M5 GS-006 / GS-009

## Scope

The Google projection boundary publishes the canonical `Grila` and `Pontaj` value matrices and treats a live projection as successful only after reading the written ranges back from Google Sheets and proving structural equality.

This contract does not authorize a live canary. CI and normal certification use fake/in-memory transports only. No Google credential, spreadsheet id, service-account material, or provider mutation is required for GS-006/GS-009 evidence.

## Canonical format v2

Both tabs start with `UGRILE_PROJECTION`, format version `v2`, followed by operational identity. The persisted reconciliation envelope contains:

- `format_version`
- `generation`
- `store_id`
- `month_id`
- `year` / `month`
- `revision`
- `rule_pack_version`
- `projected_at`
- `verification_mode`
- `verified`
- logical row counts for `Grila` and `Pontaj`
- SHA-256 for each logical tab projection
- one combined SHA-256 projection checksum

`projected_at` is pinned in the durable enqueue payload. Worker retries reuse the same value instead of manufacturing a new projection identity on every attempt.

## Checksum semantics

The canonical renderer normalizes every logical row to the fixed tab width:

- `Grila`: 5 columns
- `Pontaj`: 7 columns

Missing trailing cells are normalized to empty strings before hashing. Per-tab checksums hash the canonical logical projection, not historical blank padding used to clear an older, longer Sheet. The combined checksum hashes the two tab checksums under stable keys.

The live transport requests `UNFORMATTED_VALUE` so numbers written with `RAW` semantics are read back as values instead of locale-formatted display strings.

## Write → readback → verify

Live publication order is fixed:

1. resolve and re-attest the store Sheet binding pinned at enqueue;
2. determine existing remote row counts;
3. render canonical v2 values;
4. pad the write matrices to clear stale rows from an older, longer projection;
5. write both bounded ranges;
6. read the exact padded ranges back;
7. normalize omitted trailing blank cells/rows using Google Values API semantics;
8. compare the complete padded matrices;
9. only after a match, advance binding generation and persist a `SheetProjectionRun(status=DONE)` with reconciliation evidence.

A returned non-blank stale row or any one-cell mutation is a mismatch. Omission of trailing rows that are entirely blank is accepted because Google Values GET may omit them even when the bounded range includes them.

## Failure and retry behavior

A readback mismatch raises retryable code `GOOGLE_LIVE_READBACK_MISMATCH`. Retryable transport/quota/server failures retain the existing worker retry classification.

On mismatch or retryable transport failure:

- the binding generation is not advanced;
- no `DONE` row is written for the failed generation;
- the previous successful projection remains the last-good local snapshot;
- a failed diagnostic may be recorded without becoming last-good state.

Stale Sheet binding pins are rejected before provider I/O. A later rebind cannot redirect an already-enqueued projection job.

## Month scoping and historical snapshots

Operational reads are exact `(tenant, store, month)` reads. `metadata.month_id` is the proof that a persisted snapshot belongs to the requested month.

A projection for month A must never be returned for month B. Historical snapshots that predate month identity are intentionally fail-closed for month-scoped APIs: they can remain in the database for retention/audit, but are not presented as verified current state for an arbitrary month.

The operator endpoint is:

`GET /months/{month_id}/sheet-reconciliation?store_id=...`

It requires `sheet.read` plus exact store visibility for the requested month. Store Detail consumes this endpoint in the existing **Sincronizare & export** panel.

## Operator interpretation

Store Detail shows only operationally useful, non-secret evidence:

- verified / not verified state;
- projection revision;
- rule-pack version;
- projection timestamp;
- format version;
- shortened combined checksum.

Spreadsheet ids and credential-sensitive data are deliberately not exposed in this panel.

`verified=true` means the accepted projection was reconciled according to its provider mode. For live mode this specifically means read-after-write equality. `available=false` means no month-scoped verified snapshot exists; it must not be interpreted as a successful sync.

## Live-canary boundary

A real Google request is outside this workstream unless explicitly authorized. Live-canary evidence, if ever requested, must use a non-production Sheet and separate authorization. It is not a substitute for deterministic CI proofs and must never be triggered by normal tests.

## Completion evidence required

GS-006 / GS-009 may be marked complete only after all of the following refer to the exact final PR head:

- fake reconciliation metadata tests;
- successful live-transport readback test without network;
- checksum/readback match proof;
- one-cell mismatch proof;
- no `DONE` and unchanged binding generation on mismatch;
- last-good preservation proof;
- month A cannot leak into month B;
- legacy snapshot without `month_id` fails closed;
- reconciliation authorization/store-scope proof;
- Store Detail component rendering proof;
- real-browser Store Detail runtime coverage;
- Backend CI, Frontend CI, and Browser E2E PASS;
- SHA-guarded merge, exact main verification, and tracker evidence.
