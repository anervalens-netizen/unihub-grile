# UniHub Grile — future Retail integration contract

Status: **target contract, no Retail writes authorized**  
Program: issue #3  
Tracker: issue #4 (`INT-*`)

## 1. Purpose

This document defines what Grile must be ready to consume from UniHub Retail in
a later integration program. It exists so the plugin can be developed and
tested now without depending on the Retail implementation.

During the current program:
- Retail may be inspected read-only;
- these contracts/adapters are implemented in `unihub-grile`;
- no Retail endpoint/table/service is required to be changed yet;
- no claim is made that the real Retail adapter is already wired.

## 2. Design rule

Grile services consume Grile-owned interfaces/DTOs:

```text
External source
    |
    v
Adapter -> versioned Grile contract -> validation/generation -> Grile services
```

A fixture adapter and the future Retail adapter must implement the same semantic
contract.

Forbidden architecture:

```text
Grile domain/service -> import Retail source module
Grile domain/service -> query arbitrary Retail table directly
```

A temporary read-only DB adapter may be used later during a controlled cutover
only if explicitly approved, but it must still translate into the same Grile
contract and cannot become the permanent domain dependency.

## 3. Contract envelope

Every external snapshot/generation should carry enough metadata to answer:
- which schema version is this;
- which tenant/business context;
- which source generation;
- which period;
- when was it produced;
- is the snapshot complete;
- how can it be reconciled/replayed.

Conceptual envelope:

```json
{
  "schema_version": "retail-grile-v1",
  "tenant_id": "...",
  "timezone": "Europe/Bucharest",
  "generation": "...",
  "period": "YYYY-MM",
  "generated_at": "...",
  "datasets": {}
}
```

Unknown major schema versions fail closed. Missing required datasets cannot be
silently interpreted as all-zero business data.

## 4. Identity / principal contract

The future Retail identity adapter must produce a Grile `Principal` equivalent:

```text
user_id / subject
 tenant_id
 roles
 capabilities
 effective resource scope or inputs required to resolve it
 correlation/session context needed for audit
```

Minimum capabilities should be explicit rather than inferred from UI labels.
Candidate set:
- `grile.read`;
- `grile.schedule.read`;
- `grile.schedule.edit`;
- `grile.grid.read`;
- `grile.epay.read`;
- `grile.epay.manage`;
- `grile.sheet.sync`;
- `grile.export`;
- `grile.import`;
- `grile.jobs.read`;
- `grile.month.close`;
- `grile.month.reopen`;
- `grile.admin`.

The exact list may evolve in Grile, but the backend capability model must exist
before real Retail session wiring.

## 5. Tenant/time contract

Required:
- stable tenant identifier or deterministic mapping;
- tenant display name where useful;
- business timezone;
- active/inactive status;
- source generation.

All business dates are interpreted using the tenant business timezone; persisted
instant timestamps remain timezone-aware.

## 6. Store/catalog contract

Per store, minimum fields:
- stable external ID;
- display code/name;
- company/legal grouping needed by UI/export;
- active/effective dates where applicable;
- hierarchy/manager references required for scope;
- optional source metadata useful for reconciliation.

Names are display values, never identity keys.

## 7. Person contract

Per person, minimum:
- stable external ID;
- display name only as presentation data;
- active/effective dates;
- home store stable ID;
- employment/status fields strictly necessary for Grile;
- optional payroll master linkage if the payroll input is not supplied through a
  separate approved source.

Do not copy unnecessary personal data into Grile. CNP, private addresses and
other irrelevant HR data are outside this contract.

## 8. Manager/effective scope contract

Grile needs enough data to answer, for a business date/period:

```text
Which stores may principal X read/write?
Which people are visible through that scope?
```

The contract must support effective-dated scope changes. A current static list is
not sufficient if Retail's manager ownership can change mid-period.

The future adapter may either:
- provide normalized effective scope rows; or
- provide hierarchy facts from which Grile's centralized scope resolver derives
  the same result.

This choice must be explicit and tested.

## 9. Sales store/day contract

Minimum record:
- stable store ID;
- business date;
- amount used by the Grile rule pack;
- source generation;
- SIM quantity or other rule-pack external measures if authoritative in the same
  source;
- completeness/source metadata where necessary.

Business invariant:
- this is physical store/day truth;
- Grile derives person credit from the Grile calendar;
- changing personal attribution never changes the physical sales total.

A generation is accepted atomically or rejected; partial new input must not
replace the last accepted complete generation as if missing rows were zero.

## 10. Target contract

Minimum:
- store ID;
- period;
- target kind;
- target amount;
- authoritative selling-day divisor (`sales_days`) where required by the Mobiup
  rule pack;
- generation/source metadata.

If `sales_days` is required and missing, the condition is an explicit anomaly.
Preview fallback behavior, if any, must not make final close appear fully valid.

## 11. Incentive and other external payroll inputs

The rule pack currently needs an authoritative monthly incentive input. Future
Retail integration must supply it through a versioned contract or an explicitly
separate authoritative connector.

Any additional external payroll input follows the same rule:
- source identified;
- period/person/store linkage explicit;
- generation/version explicit;
- missing input never silently becomes authoritative zero unless the business
  contract explicitly defines zero as the correct default.

Salary/tickets/Flip master remains a Grile-owned normalized input boundary even
if the source is later Retail/HR.

## 12. Generation semantics

A connector import must be replayable and diagnosable.

Required properties:
- source generation ID;
- validated dataset manifest/counts;
- atomic accepted generation;
- last-good retention;
- idempotent replay where practical;
- no cross-tenant identity collision;
- audit/job linkage;
- current vs previous generation visible operationally.

## 13. Adapter interfaces

The exact Python names are implementation decisions, but the architectural roles
should remain separable:

```text
IdentityAdapter / PrincipalProvider
CatalogAdapter
ScopeAdapter
SalesAdapter
TargetAdapter
Incentive/PayrollInputAdapter
```

A single snapshot provider may implement several interfaces internally, but
business services should not depend on a giant Retail-specific object.

## 14. FixtureRetailAdapter

Before real integration, Grile must provide anonymized fixtures that implement
the same semantic contract and include:
- multiple managers/scopes;
- at least 75 stores / 150 people for performance fixtures;
- effective scope changes;
- missing/duplicate source cases;
- sales/target generation changes;
- incentive/payroll input edge cases.

Contract tests should be reusable against both fixture and future Retail adapter.

## 15. Frontend/shell integration contract

The final integration is expected to need only bounded Retail-side work:
- navigation/menu entry or route mount;
- authenticated session/principal handoff;
- capabilities;
- deep links between Retail and Grile if useful;
- shared visual tokens/components where chosen;
- reverse proxy/API routing/deployment wiring.

The standalone Grile frontend may later be:
1. mounted as a separately served module behind Retail navigation; or
2. ported into the Retail shell/shared component layer.

The choice is deferred. Current Grile UX must not depend on either choice.

## 16. What Grile must NOT require from Retail

- direct write access to Retail DB;
- Grile-specific financial formulas implemented in Retail;
- Google Sheets handled by Retail;
- duplication of Grile calendar/pontaj engine inside Retail;
- synchronous Google/export work in Retail requests;
- arbitrary Retail internal Python/TypeScript imports.

## 17. Read-only Retail inspection during current development

Allowed:
- inspect architecture docs/source contracts;
- inspect auth/capability conventions;
- inspect current schemas/read APIs relevant to mapping;
- inspect shared UI conventions;
- update this Grile contract/fixtures based on findings.

Not allowed:
- commits/PRs in Retail;
- migrations;
- service restart/deploy;
- production data mutations;
- changing Retail auth/config to make Grile easier to develop.

## 18. Integration-ready gate

`M6-GATE` in issue #4 passes only when:
- versioned DTO/interfaces exist in Grile;
- fixtures implement them;
- contract tests exist;
- services/domain do not depend on Retail details;
- unsupported versions fail closed;
- generation/last-good semantics are tested;
- identity/capability handoff shape is documented;
- expected future Retail-side changes are explicit and minimal;
- Retail has still not been modified by this program.
