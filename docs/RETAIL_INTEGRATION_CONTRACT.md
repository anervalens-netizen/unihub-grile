# UniHub Grile — future Retail integration contract

Status: **Grile-side adapter contract implemented; no Retail writes authorized**  
Program: issue #3  
Tracker: issue #4 (`INT-*`)

## 1. Purpose

This document defines what Grile is prepared to consume from UniHub Retail in
a later integration program. It exists so the plugin can be developed and
tested without depending on Retail implementation modules.

During the current program:
- Retail may be inspected read-only;
- these contracts/adapters are implemented in `unihub-grile`;
- no Retail endpoint/table/service is required to be changed yet;
- no claim is made that the real Retail adapter or Retail identity handoff is
  already wired.

The current read-only mapping inventory is pinned in
`docs/RETAIL_MAPPING_INVENTORY.md`; its Retail reference SHA is evidence, not a
runtime dependency.

## 2. Design rule

Grile services consume Grile-owned interfaces/DTOs:

```text
External source
    |
    v
Adapter -> versioned Grile contract -> validation/generation -> Grile services
```

`FixtureRetailAdapter` and the future Retail adapter implement the same
`RetailSnapshotAdapter` semantic boundary. Neither the Grile domain nor service
layer imports Retail code.

Forbidden architecture:

```text
Grile domain/service -> import Retail source module
Grile domain/service -> query arbitrary Retail table directly
```

A temporary read-only DB adapter may be used later during a controlled cutover
only if explicitly approved, but it must still translate into the same Grile
contract and cannot become the permanent domain dependency.

## 3. Executable v1 contract envelope

The supported schema is exactly `retail-grile.v1`, represented by
`RetailSnapshotV1` in `backend/src/ugrile/connectors/retail_contract_v1.py`.
The top-level snapshot contains:

```json
{
  "schema_version": "retail-grile.v1",
  "tenant_id": "tenant_example",
  "timezone": "Europe/Bucharest",
  "period": "YYYY-MM",
  "generation": {
    "sales_hash": "source-authority-hash",
    "sales_revision": 12,
    "campaign_revision": 7,
    "cutoff_date": "YYYY-MM-DD",
    "generated_at": "timezone-aware timestamp"
  },
  "complete": true,
  "stores": [],
  "people": [],
  "manager_scopes": [],
  "sales_store_day": [],
  "targets": [],
  "incentives": [],
  "payroll_inputs": []
}
```

`parse_retail_snapshot()` negotiates the schema before Pydantic validation.
Unknown versions and malformed/incomplete snapshots fail closed. Tenant and
period returned by an adapter are rechecked against the caller request before
any persistence mutation.

`complete=true` is a semantic assertion: omission cannot silently mean a
monetary zero. A business zero must be represented as an explicit authoritative
row where the relevant dataset is required.

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
before real Retail session wiring. Until INT-012 defines manager identity
translation, a snapshot carrying `manager_scopes` is rejected by the ingest
bridge rather than guessed into Grile `User` foreign keys.

## 5. Tenant/time contract

Required:
- stable tenant identifier or deterministic mapping;
- tenant display name where useful;
- business timezone;
- active/inactive status;
- source generation.

All business dates are interpreted using the tenant business timezone; persisted
instant timestamps remain timezone-aware. A snapshot timezone that conflicts
with an existing Grile tenant fails closed.

## 6. Store/catalog contract

Per store, minimum fields:
- stable external ID;
- display code/name;
- company/legal grouping needed by UI/export;
- active/effective dates where applicable;
- hierarchy/manager references required for scope;
- optional source metadata useful for reconciliation.

Names are display values, never identity keys. The M6 bridge derives stable
Grile internal codes from the external IDs while preserving the external IDs on
catalog rows for reconciliation.

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

A future adapter must resolve one defensible payroll/home store for the requested
period before constructing `RetailPersonV1`; ambiguous multi-store source facts
fail closed rather than being guessed.

## 8. Manager/effective scope contract

Grile needs enough data to answer, for a business date/period:

```text
Which stores may principal X read/write?
Which people are visible through that scope?
```

The contract supports effective-dated scope rows. A current static list is not
sufficient if Retail manager ownership changes across periods.

The current DTO is `RetailManagerScopeV1`. Persistence is deliberately deferred
until INT-012 establishes the Retail-subject/group-to-Grile-user identity
adapter; the M6 ingest bridge rejects unresolved scope rows rather than silently
dropping or misbinding them.

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

Each accepted snapshot receives a Grile-owned `generation_key` derived from the
canonical snapshot SHA-256. Historical `SalesStoreDay` generations remain
stored, but attribution, grid and close paths consume only the generation named
by the current accepted Retail ledger head for that tenant/period. This prevents
multiple complete generations from being summed together.

## 10. Target contract

Minimum:
- store ID;
- period;
- target kind;
- target amount;
- authoritative selling-day divisor (`sales_days`) where required by the Mobiup
  rule pack;
- generation/source metadata.

Retail target rows are stored as versioned Grile rows. The accepted generation
ledger records the exact `(store_id, kind, version)` rows belonging to that
snapshot. For a Retail-backed period, grid and close do **not** select an older
"latest" row if the current accepted snapshot omits the target. The condition is
surfaced as `TARGET_INPUT_MISSING` and is close-blocking. A present target whose
amount is genuinely zero remains distinct and is reported through the existing
zero-target anomaly.

If `sales_days` is required and missing, the condition remains an explicit
anomaly. Preview fallback behavior cannot make final close appear fully valid.

## 11. Incentive and other external payroll inputs

The rule pack currently needs an authoritative monthly incentive input. Future
Retail integration must supply it through this versioned contract or an
explicitly separate authoritative connector.

Accepted Retail incentive rows are pinned as exact `(person_id, version)` ledger
entries. Omission in a newer complete snapshot never reactivates an older
version and never becomes authoritative zero. Grid may use zero as a deterministic
calculation placeholder, but emits `INCENTIVE_INPUT_MISSING`; the close policy
blocks that anomaly. An actual no-campaign zero is represented by an explicit
incentive row with amount zero. `FixtureRetailAdapter` follows this same rule.

Generic `payroll_inputs` remain fail-closed in the ingest bridge until each
`input_kind` has an explicit Grile persistence/business mapping. Salary/tickets/
Flip master remains a Grile-owned normalized boundary even if its eventual
source is Retail/HR.

## 12. Accepted generation and last-good semantics

The semantic snapshot SHA-256 is calculated from canonical `RetailSnapshotV1`
content while excluding `generation.generated_at`, because fetch time is
diagnostic rather than source identity. The first 32 hex characters form the
DB-compatible sales `generation_key`; the full SHA remains in the ledger.

A successful apply writes both normalized inputs and one `ImportRun` ledger row
inside the same caller transaction. Ledger kind is period-scoped:

```text
RETAIL_SNAPSHOT_V1:YYYY-MM
```

The newest committed `DONE` ledger row for `(tenant, period)` is the accepted
head. Properties:
- exact replay returns `REPLAYED` and creates no new financial versions/ledger
  row;
- source revision/cutoff regression is rejected;
- a changed sales hash without sales revision advance is rejected;
- changed content with neither sales nor campaign revision advance is rejected;
- the tenant row is locked before transition validation and version allocation,
  preventing concurrent writers from publishing an older head behind a newer
  one;
- a failed/rolled-back attempt cannot replace the previous committed head;
- target and incentive ledger entries are strictly validated and duplicate
  identities fail closed;
- persisted grid financial freshness is revalidated against the **current
  accepted head**, even when `Month.revision` has not changed.

Legacy fixture/non-Retail periods preserve their historical latest-version
behavior only when no accepted Retail head exists.

## 13. Adapter interfaces and service boundary

The executable M6 source adapter boundary is intentionally small:

```python
class RetailSnapshotAdapter(Protocol):
    def load_snapshot(self, *, tenant_id: str, period: str) -> RetailSnapshotV1: ...
```

Adapters perform source acquisition + translation to the Grile-owned DTO. They
do not mutate Grile persistence. `RetailSnapshotIngestService` is the Grile-owned
mutation boundary that validates request identity/time, serializes generation
acceptance and applies the normalized snapshot.

Identity/session is intentionally a separate future adapter (INT-012). This
keeps Retail auth/provider details out of financial data adapters and out of the
domain.

## 14. FixtureRetailAdapter

`FixtureRetailAdapter` is implemented from the package's deterministic fixture
and returns the exact same `RetailSnapshotV1` type required from a future Retail
adapter. It does not call a different domain path. It includes explicit zero
incentive rows for people with no campaign amount so tests distinguish
"authoritative zero" from "input omitted".

Contract/integration tests cover:
- supported/unsupported schema negotiation;
- tenant/period matching;
- idempotent replay;
- accepted generation advance and stale-generation rejection;
- last-good preservation;
- physical sales reading from only one accepted generation;
- target/incentive omission without stale fallback;
- payroll-grid auto-selection of the accepted generation;
- close-time invalidation of a grid when the Retail head advances without a
  calendar revision change.

Large 75-store/150-person performance fixtures remain a performance-proof
concern; they do not change the adapter contract.

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
