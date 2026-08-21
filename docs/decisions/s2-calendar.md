# Historical S2 calendar and schedule-import decisions

> **Historical context only.** These decisions record the calendar/import design
> that produced the current implementation. Stage sequencing and temporary auth
> language are obsolete; current direction/status are issues #3/#4.

## D-S2-01 — Calendar is the single write authority

Whole-calendar changes are applied through the calendar service with a locked
month revision/CAS check, full candidate validation and coherent replacement of
working assignments plus `OFF`/`LEAVE` rows. Compatibility endpoints must not
become a second business authority.

**Current standing:** preserve the single calendar authority and CAS invariant.
Performance may be optimized under `BE-*` only if behavior remains equivalent.

## D-S2-02 — Manager scope is effective-dated and deny-by-default

`manager_scopes` represents tenant-safe `(user, store, effective_from,
effective_to)` windows. Admin behavior and manager resource access are resolved
from explicit scope rules rather than free store IDs.

The original development identity headers were a temporary bootstrap seam.
**Current standing:** effective-dated deny-by-default scope remains; authentication
and resource-level enforcement are being centralized under `SEC-*`.

## D-S2-03 — Pontaj is derived, not independent business truth

Pontaj is generated from calendar state. The accepted Mobiup interval/break/net
hours are now canonical in `docs/MOBIUP_RULE_PACK.md` rather than an unconfirmed
stage input.

**Current standing:** no second manual Pontaj authority; changes to Mobiup policy
require versioned rule-pack changes.

## D-S2-04 — XLSX import is manifest/CAS based

The generated workbook binds tenant/month/base revision and technical IDs. Preview
is read-only; apply rejects malformed, unknown, duplicate, out-of-scope and
stale input before/within the authoritative calendar transaction as appropriate.

**Current standing:** preserve preview + explicit atomic apply + revision/scope
protection. Harden UX, scope and verification under the current tracker.
