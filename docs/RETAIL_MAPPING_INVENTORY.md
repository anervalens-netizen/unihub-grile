# Retail → Grile read-only mapping inventory

Status: **INT-001 evidence / design input only**  
Retail mutation authority: **none**  
Retail reference repository: `anervalens-netizen/unihub-retail`  
Retail reference commit: `54819a8c61832ca95a831ce26793c0f207b0e4dd`  
Grile tracker: #4 (`INT-*`)

This inventory records the current Retail seams that are relevant to the future
Grile adapter. It is deliberately pinned to an exact Retail commit so later
contract work can distinguish a real source change from an assumption drift.

No Retail code, schema, configuration, deployment or data was modified while
building this inventory. All implementation remains Grile-owned.

## 1. Architectural boundary

Retail's architecture contract declares the normal backend flow as:

```text
router -> service -> repository
```

and forbids router database access and domain-to-infrastructure imports. The
same contract lists existing Grile-related orchestration boundaries such as
`services.grile_worker_jobs` and `services.grile_pilot_v2_sync`.

Implication for M6:

- Grile must not import Retail Python modules or depend on Retail repository
  classes;
- future integration should expose a stable read contract at a Retail service /
  export boundary and translate it through a Grile-owned adapter;
- a direct DB reader, if ever used temporarily during controlled cutover, must
  still translate into the same Grile DTOs and cannot become a domain
  dependency.

Reference:
`unihub-retail/backend/architecture_contract.json` at the pinned SHA.

## 2. Current Retail identity seam

Retail authentication is Authentik OIDC. The normalized `AuthClaims` object
contains:

- `sub` — stable authenticated subject;
- `email`;
- `preferred_username`;
- `groups`;
- issuer/audience and token timestamps.

Retail also has a localhost-only Hub internal-secret path that produces a
service identity. That path is an internal service mechanism and **must not** be
used as a human Grile principal mapping.

M6 mapping:

| Grile concept | Retail source | Rule |
| --- | --- | --- |
| principal subject | OIDC `sub` | stable external subject; never display name identity |
| display/login metadata | `preferred_username`, `email` | presentation/contact only |
| role/capability inputs | OIDC `groups` | adapter translates through an explicit versioned mapping |
| tenant | no tenant claim proven in the inspected seam | deployment/config mapping is required; never infer from email/group text |
| correlation/session | Retail request/session boundary | future adapter shape only; do not copy tokens into Grile |

Reference: `unihub-retail/backend/auth.py` at the pinned SHA.

## 3. Store/catalog source

The canonical Retail store identity is `stores.site_code`. Current store rows
contain at least:

```text
site_code
locatie
firma
regional
asm
team_leader_id
is_active
first_seen_month
last_seen_month
updated_at
```

`site_code`, not `locatie`, is the identity candidate. Names and organizational
labels are display/business attributes.

Retail also persists `store_activity_events`; Grile does not need to ingest
those mutation events to consume the current/effective catalog.

M6 mapping:

| Grile field | Retail field / source |
| --- | --- |
| `external_store_id` | `stores.site_code` |
| display name | `stores.locatie` |
| company/legal grouping | `stores.firma` |
| active | `stores.is_active` |
| observed range | `first_seen_month`, `last_seen_month` when useful for reconciliation |
| org labels | effective source in §4, not blindly current `stores.asm/regional` for history |

References:
- `unihub-retail/backend/db/schema_v2.sql`
- `unihub-retail/backend/repositories/stores.py`
- `unihub-retail/backend/services/stores.py`
all at the pinned SHA.

## 4. Effective manager / organization scope

Retail already has an effective-dated source:

```text
store_org_assignments
  site_code
  regional
  asm
  valid_from_month
  valid_to_month
  is_current
  source
```

Therefore the future Grile adapter must **not** use only current
`stores.asm/regional` to reconstruct historical manager scope.

Proposed semantic translation:

```text
Retail store_org_assignments
        -> RetailOrgAssignment DTO
        -> Grile effective manager/store scope rows
```

Rules:

- `valid_from_month`/`valid_to_month` define the authoritative monthly interval;
- overlapping/ambiguous intervals for the same store fail closed during adapter
  validation;
- `is_current` is a convenience marker, not a substitute for interval logic;
- current `stores.asm/regional` may be used only for current display/reconciliation,
  never to rewrite history.

This is the primary source candidate for **INT-005**.

Reference: `unihub-retail/backend/db/schema_v2.sql` at the pinned SHA.

## 5. People / employment / home-store source

Retail's reporting model identifies agents by the `agent` code. Relevant
monthly reporting sources include:

- `reporting_agent_month` — agent + month + site/hierarchy reporting facts;
- `reporting_agent_lifecycle_month` — monthly lifecycle flags/history;
- `reporting_agent_day` — daily sales attribution by agent/site/date.

The existing Retail Grile target-sync also reads `reporting_agent_month` by
`import_month` and `site_code` to resolve the set of Retail agents for a store.

For Grile, the minimum person contract should use the stable agent code as the
external identity and copy only necessary employment/scope facts.

Important ambiguity rule:

- one `(agent, month)` associated with exactly one defensible home store may be
  translated into a monthly home-store interval;
- if Retail contains multiple store associations for the same agent/month and
  no stronger source proves which is the payroll home, the adapter must fail
  closed for that person's home-store mapping rather than pick one;
- day-level sales attribution is **not** automatically payroll home-store
  authority.

Privacy boundary:

Retail also has `agent_salary_links`, including `salary_cnp`. CNP/private HR
identifiers are explicitly outside the Grile Retail contract and must not be
copied merely because they exist in Retail.

This inventory therefore does **not** nominate `agent_salary_links.salary_cnp`
as an integration field.

References:
- `unihub-retail/backend/db/schema_v2.sql`
- `unihub-retail/backend/services/agents.py`
- `unihub-retail/backend/services/grile_agent_targets.py`
at the pinned SHA.

## 6. Physical sales store/day and generation lineage

Retail already has a strong generation seam used by its current isolated Grile
V2 pilot.

`services.grile_pilot_v2_sync.load_pilot_v2_source()` reads in one read-only,
repeatable-read transaction:

- `reporting_sales_cutoff_v1` for the accepted cutoff date;
- the promoted sales head from `retail_outbox_events` event
  `retail.sales_generation_promoted.v1`, yielding generation hash + revision;
- `reporting_agent_day` for daily store/agent sales through the cutoff;
- store hierarchy from `stores`;
- official monthly targets from `store_targets`;
- SIM quantities from `reporting_cartela_day`;
- campaign incentive values from `reporting_campaign_month_v3`.

The same service separately fences publication against `sales_generation_heads`
+ `import_snapshots.manifest_sha256` so a stale source cannot be published after
the sales head advances.

For Grile physical store/day truth, the future adapter may aggregate the
accepted-generation `reporting_agent_day.total_sales` rows by `(site_code,
sale_date)` exactly as the existing Retail Grile pilot already derives store
sales. The adapter must preserve the accepted sales generation hash/revision and
cutoff metadata; missing rows after cutoff are not authoritative zeroes.

Required future DTO semantics for **INT-006**:

```text
external_store_id
business_date
amount
currency
source_generation
source_revision
cutoff / completeness metadata
```

SIM quantity may travel in the same generation only if the contract explicitly
states that it is complete and authoritative for the requested period.

Reference: `unihub-retail/backend/services/grile_pilot_v2_sync.py` at the pinned
SHA.

## 7. Targets

Retail official store targets are stored in:

```text
store_targets
  import_month
  site_code
  target_value
  source_file
  created_at
```

The current table is one official target per `(import_month, site_code)`; target
scenario/planning tables are a separate concern and must not be mistaken for the
official accepted target.

M6 contract requirements:

- store identity and period explicit;
- amount/currency explicit in the Grile DTO;
- source generation/manifest metadata supplied by the adapter envelope even if
  the Retail row itself has no generation column;
- Grile's required `sales_days` cannot be invented from calendar length. If no
  authoritative Retail source is proven for it, the field remains missing and
  Grile raises its existing anomaly/fail-closed condition.

This is part of **INT-007**.

References:
- `unihub-retail/backend/db/schema_v2.sql`
- `unihub-retail/backend/services/grile_pilot_v2_sync.py`
at the pinned SHA.

## 8. Incentive / other payroll-required external inputs

The current isolated Retail Grile V2 pilot reads incentive results from
`reporting_campaign_month_v3` for:

```text
period
site_code
agent
incentive_eligible_quantity
incentive_value
incentive_potential
status
mechanism = 'incentive'
mechanism_variant = 'incentive'
```

The future contract should expose only the authoritative value and the minimum
identity/status metadata needed by the rule pack and reconciliation. Grile must
not recalculate the Retail campaign engine inside the adapter.

If the campaign authority head differs across rows for the requested period, the
snapshot must fail validation rather than silently combine revisions. The current
pilot already checks a single campaign authority head before publication.

Salary/tickets/Flip or other HR-origin inputs remain separate normalized Grile
boundaries until a specific authoritative Retail/HR source is proven. Missing
input never becomes authoritative zero by default.

This is part of **INT-007**.

Reference: `unihub-retail/backend/services/grile_pilot_v2_sync.py` at the pinned
SHA.

## 9. Generation / completeness contract that M6 should preserve

The strongest current Retail precedent is not a loose collection of table
reads; it is the pinned source-head pattern in the existing Grile pilot.

The Grile adapter contract should therefore carry an envelope similar to:

```json
{
  "schema_version": "retail-grile.v1",
  "tenant_id": "<explicit mapping>",
  "timezone": "Europe/Bucharest",
  "period": "YYYY-MM",
  "generation": {
    "sales_hash": "...",
    "sales_revision": 1,
    "campaign_revision": 1,
    "cutoff_date": "YYYY-MM-DD"
  },
  "complete": true,
  "datasets": {}
}
```

Exact field names are owned by subsequent INT contract work, but these invariants
are already fixed by evidence:

1. schema major version is explicit;
2. source generation/revisions are explicit;
3. datasets are validated as one snapshot before apply;
4. a newer partial/invalid snapshot cannot destroy the last accepted complete
   generation;
5. missing required data is not converted to zero;
6. unsupported major versions fail closed;
7. replay of the same generation is idempotent where practical.

## 10. Current Grile connector gap analysis

Grile currently owns `grile.connector.v1` with fixture records for stores,
people, sales, targets and incentives. It is intentionally fixture-bound:
`ConnectorHeader.generation` accepts only `FIXTURE_V1`.

That existing contract is useful but insufficient for M6 because it does not yet
model all of:

- source schema negotiation independent of fixture generation;
- tenant timezone in the envelope;
- explicit source completeness/manifest;
- effective Retail org assignments;
- defensible employment/home-store intervals;
- source-head sales/campaign lineage;
- unsupported-version fail-closed negotiation across a future Retail adapter;
- a common adapter protocol implemented by both fixture and future Retail
  sources.

M6 should extend/replace this boundary cleanly rather than teaching domain
services about Retail table names.

Reference: `backend/src/ugrile/connectors/v1_types.py` in this repository.

## 11. Source-to-contract matrix

| M6 task | Current Retail evidence at pinned SHA | Grile-side target |
| --- | --- | --- |
| INT-002 tenant/time/generation | OIDC has no proven tenant claim; sales head/cutoff lineage exists | explicit tenant mapping + timezone + generation envelope |
| INT-003 store/catalog | `stores` | versioned Store DTO keyed by `site_code` |
| INT-004 people/employment/home store | `reporting_agent_month`, lifecycle; monthly store association | minimal Person/Employment DTO + ambiguity guard |
| INT-005 manager/effective scope | `store_org_assignments` | versioned effective store-scope rows |
| INT-006 sales store/day | accepted-generation `reporting_agent_day`, sales head/cutoff | physical store/day DTO with generation/completeness |
| INT-007 targets/incentives/payroll inputs | `store_targets`, `reporting_campaign_month_v3`, separate HR boundaries | versioned external-input DTOs, missing != zero |
| INT-008 fixture adapter | existing Grile fixture connector | implement same new snapshot/provider protocol |
| INT-009 domain isolation | current Grile connector seam | no Retail module/table awareness outside adapter |
| INT-010 negotiation | no cross-repo contract yet | explicit supported major versions; unknown fail closed |
| INT-011 last-good | Retail source-head precedent + Grile job/import semantics | atomic validated generation + last-good retention |
| INT-012 identity | Retail `AuthClaims` | documented PrincipalProvider translation contract |
| INT-013 shell | current Retail shell inspected later only as needed | navigation/session/capability/deep-link contract |
| INT-014 future Retail changes | this inventory identifies source seams | minimal future read/export + identity/shell wiring only |

## 12. Explicit non-contract fields / anti-coupling list

The future adapter must not make Grile depend on:

- Retail Python import paths or repository classes;
- Retail database primary keys that are not stable business identities;
- current-only `stores.asm/regional` for historical scope;
- Google Sheet ids or the old Retail-managed Grile Google workflow;
- `agent_salary_links.salary_cnp` or unrelated HR/personal data;
- Retail UI labels as authorization capability names;
- Retail target planning scenarios as if they were accepted official targets;
- direct writes to any Retail table;
- Retail's existing Grile pilot implementation as permanent business logic.

The existing Retail Grile pilot is evidence for source semantics and lineage, not
an implementation dependency.

## 13. INT-001 conclusion

The pinned Retail revision already contains enough stable read semantics to
design the Grile-owned v1 integration contracts without changing Retail:

- stable store code;
- effective-dated store organization assignments;
- monthly agent/reporting identity;
- promoted sales generation lineage and cutoff;
- store/day sales derivation used by the current pilot;
- official store targets;
- campaign incentive authority;
- OIDC subject/group identity inputs.

The material gaps are contract-level, not a need to modify Retail now. Subsequent
INT work belongs entirely in `unihub-grile` until the later integration milestone
explicitly authorizes Retail-side changes.