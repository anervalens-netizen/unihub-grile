# Google Sheet E-pay input and protection contract

Status: GS-007 / GS-008 operational contract for the standalone plugin candidate.

This document defines the only Google Sheet cells that may be edited by an operator and the exact readback rules used to turn those cells into E-pay observations. It does not authorize a production/live canary.

## Bound tabs and ownership

A store must already have the stable binding defined in `google-sheet-binding.md`:

- one bound spreadsheet;
- one bound `Grila` tab;
- one bound `Pontaj` tab.

Projection and E-pay readback never discover, create or rebind this identity. A stale/missing binding fails closed.

## Column ownership

The `v2` managed projection remains unchanged:

- `Grila!A:E` — application-managed projection; read-only to operators;
- `Pontaj!A:G` — application-managed projection; read-only to operators.

E-pay uses a separate block in the bound Grila tab:

- `Grila!G:I` — E-pay control/input block;
- column `G` — application-managed `person_id`; never operator-editable;
- column `H` — operator input `UNDER_50`;
- column `I` — operator input `AT_OR_OVER_50`.

Keeping E-pay outside `A:E` is intentional: legitimate operator edits must not invalidate the deterministic projection checksum from GS-006.

## E-pay block format v1

The block is bounded to at most 256 expected people and uses stable sorted person IDs.

| Row | G | H | I |
|---|---|---|---|
| 1 | `UGRILE_EPAY_INPUTS` | `v1` | blank |
| 2 | `month_id` | exact month id | blank |
| 3 | `revision` | exact calendar-data revision | blank |
| 4 | `person_id` | `UNDER_50` | `AT_OR_OVER_50` |
| 5+ | exact person id | editable value | editable value |

A projection refresh preserves H/I raw values only when the existing remote block has a valid marker/header and belongs to the same month. A different month, duplicate person identity or ambiguous block is not copied forward.

When the expected person set changes, the projection rewrites the bounded block and clears stale trailing rows. The new person set is derived from actual `WORKING` assignments for that store/month.

## Readback semantics — GS-007

Operational endpoint:

`POST /months/{month_id}/epay/google-readback?store_id=...`

Authorization:

- capability: `epay.write`;
- exact requested store must be in scope for the month;
- known CLOSED month fails before Google I/O;
- local persistence reuses the locked financial-write gate and existing E-pay validation/audit path.

The Google read is bounded to `Grila!G1:I260` and uses unformatted values. It never reads arbitrary workbook ranges.

Before any local observation is committed, the service revalidates under locks:

1. complete Sheet binding identity;
2. current calendar-data revision;
3. exact working-person set.

If any local identity changed while the network read was in flight, the remote values are discarded and the current expected cells are persisted as invalid observations. This prevents an older valid E-pay snapshot from making the current month appear fresh after a stale read.

Remote structure is valid only when all of these match exactly:

- marker/version;
- month id;
- calendar-data revision;
- header categories;
- one unique row for every expected person;
- no additional person row.

A structural mismatch is not treated as missing data or zero. The latest expected cells are persisted invalid, therefore freshness fails closed while the existing last-good valid values remain available for diagnostic/grid-preview semantics already defined by the E-pay service.

Cell values continue to use the canonical E-pay validation contract:

- exactly `UNDER_50` and `AT_OR_OVER_50` per expected person;
- integer values `0..10` valid;
- blank/non-integer/out-of-range values invalid;
- invalid latest observations block freshness/close according to the active close policy.

A transport/network failure does not overwrite last-good local values because no trustworthy readback occurred.

## Protection contract — GS-008

Protection is reconciled by the live projection path before projection values are accepted as DONE.

For each bound tab the application owns one protected range identified by the description prefix:

`UGRILE_MANAGED_PROTECTION:v1:`

Required state:

### Grila

- one enforced whole-sheet protected range;
- protected-range editors are exactly the service-account identity plus any
  file owner identities returned by Google Drive metadata; owners cannot be
  excluded by a Sheets protection;
- domain-wide edit is disabled;
- the only unprotected cells are `H:I` on the exact current person rows (zero-based columns 7..9, rows 4..4+N);
- metadata rows, person IDs, projection columns and all other cells remain protected.

### Pontaj

- one enforced whole-sheet protected range;
- protected-range editors are exactly the service-account identity plus the
  Google-reported file owners;
- no unprotected ranges.

The reconciliation is idempotent. If the managed protection already matches exactly, no structural `batchUpdate` is sent. This avoids quota churn.

Managed duplicates are removed, but protections not owned by UniHub Grile are never deleted. Any non-owner extra editor on a managed protection is removed during reconciliation and cannot satisfy attestation. If a non-warning external protection overlaps the required E-pay input cells, or a named/table protection prevents proving editability, projection fails closed with `GOOGLE_EPAY_PROTECTION_CONFLICT` rather than weakening or deleting the external control.

After any managed protection change, control state is read again and must attest exactly before the projection values can be marked successful.

## Failure and last-good rules

A live projection is successful only after:

1. binding pin validation;
2. E-pay block preservation/rendering;
3. protection reconcile + re-attestation;
4. managed projection write;
5. exact managed projection readback;
6. E-pay structural readback;
7. only then binding generation advancement and local `SheetProjectionRun` DONE.

Protection conflict, layout mismatch, projection mismatch or provider error cannot create a false DONE and cannot advance the binding generation. Existing last-good projection remains readable.

Operator-entered H/I values are deliberately excluded from the GS-006 projection checksum. Their correctness is represented by E-pay observation validity/freshness, not by the projection checksum.

## Secrets and diagnostics

- credentials remain external to the repository;
- spreadsheet IDs are operational binding data and are not returned by the E-pay readback response;
- credential contents are never logged or returned;
- provider errors expose typed codes/status, not secrets;
- live E-pay reads require `GOOGLE_PROVIDER=live` plus a valid external credential file;
- Google E-pay readback itself does not require the Google mutation opt-in because it performs only a remote values GET; local E-pay persistence is still a protected financial write;
- projection-side protection/value writes continue to require the explicit Google mutation opt-in.

## Live canary boundary

CI must use fake/recording transports only. This workstream does not perform a real Google request.

Before a future live canary, evidence must identify the exact test spreadsheet/store binding and explicitly authorize provider mutation. The canary must verify:

- exact managed protections;
- only expected H/I cells editable;
- Pontaj fully read-only;
- E-pay values survive projection refresh;
- exact readback produces the expected observations;
- no unrelated workbook content/protection is altered.

## Completion evidence

GS-007 / GS-008 are not complete merely because this contract exists. Completion requires implementation, adversarial tests, exact-head required CI, SHA-guard merge, independent `main` verification and an evidence overlay in tracker issue #4.
