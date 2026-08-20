# S2 calendar and schedule import decisions

## D-S2-01 — Calendar is the single write authority

Whole-calendar changes are applied by `CalendarService.apply`. The service locks
and CAS-checks the month revision, validates the full candidate calendar, then
replaces working assignments and `OFF`/`LEAVE` rows in one transaction. The
legacy single-assignment endpoint remains only as a compatibility facade over
the same service.

## D-S2-02 — Manager scope is effective-dated and deny-by-default

`manager_scopes` stores tenant-safe `(user, store, effective_from,
effective_to)` windows. Admins are tenant-wide; managers need an active scope
row for both the person home store and the target store on the edited date.
The development identity seam remains the existing `X-Ugrile-Identity` plus
`X-Ugrile-Tenant` header; production authentication is still outside S2.

## D-S2-03 — Pontaj hours remain configuration, not business truth

The projection layer accepts `HoursConfig(start, end, pause_minutes)` and uses
`10:00–22:00` with a 60-minute pause only as a safe development default. The
exact client interval, break policy, holidays and salary treatment remain
unconfirmed product inputs and must be approved before S3 calculations.

## D-S2-04 — XLSX import is manifest/CAS based

The generated workbook carries tenant, month and base revision in the hidden
`Manifest`; technical person IDs are hidden on manager tabs, and only day cells
are unlocked for dropdown edits. Preview never writes. Apply rejects malformed,
unknown, duplicate, out-of-scope and stale-revision input before the calendar
transaction begins.
