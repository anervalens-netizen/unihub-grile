# Retail host integration contract

Status: **Grile-side contract only; Retail remains untouched**  
Program: issue #3  
Tracker: issue #4 (`INT-012`, `INT-013`, `INT-014`)

This document closes the remaining M6 host-integration design work without
performing the real Retail integration. It defines the smallest trusted seam
needed later for identity/session handoff, shell/navigation mounting and
Retail-side wiring.

## 1. Boundary rule

Retail remains a host/source system. Grile remains the authority for:

- payroll and schedule domain behavior;
- the final application role/capability model;
- effective-dated store/person resource scope;
- close/reopen rules;
- Google/XLSX behavior;
- worker/idempotency semantics.

The future host may authenticate a session and provide a **capability ceiling**,
but it may not widen a Grile role or bypass Grile resource-scope checks.

No current runtime path accepts a Retail token. The standalone development
provider remains the only installed principal provider until a later integration
milestone is explicitly opened.

## 2. INT-012 — identity/session/capability adapter

The executable normalized contract is
`backend/src/ugrile/services/retail_identity.py`.

Schema: `retail-grile.identity.v1`.

A trusted Retail adapter must authenticate the host session first and then emit:

- stable host `subject`;
- Grile tenant id after deterministic tenant mapping;
- normalized Grile role (`ADMIN`, `MANAGER`, `READONLY`);
- finite Grile capability names;
- display email;
- opaque host session id;
- optional correlation id;
- timezone-aware issued/expiry timestamps.

Unknown fields, unknown capabilities, unsupported schema versions and invalid
session timestamp bounds fail closed at the normalized contract boundary.

### 2.1 Subject mapping

`subject` is the external identity key. Email is display data and must never be
the durable identity key.

The future adapter must map `(tenant_id, subject)` to one approved Grile user
before returning a `Principal`. It must not silently create or merge users based
on name/email similarity.

Manager scope remains a separate effective-dated business mapping. Host identity
claims do not substitute for `manager_scopes` history.

### 2.2 Capability authority

`Principal.capability_ceiling` is optional:

- `None` keeps current standalone behavior: the Grile role supplies the complete
  capability set;
- a finite ceiling narrows the role for one trusted host session;
- `capabilities_for()` always intersects the ceiling with the Grile role;
- `apply_retail_capability_ceiling()` additionally rejects an explicit host
  over-claim instead of silently treating it as authority.

Therefore a Retail `MANAGER` handoff can omit `schedule.write` and become more
restricted, but it cannot claim `month.close` if the Grile `MANAGER` role does
not own it.

The frontend continues to consume only `GET /session`. It never trusts host UI
labels or raw host claims directly.

### 2.3 Session/token handling

Later integration must satisfy all of the following:

1. Retail/host authentication is verified before normalized claims are created.
2. Raw bearer/session tokens are never persisted in Grile and never logged.
3. Expiry is checked on every request or by a host-verified short-lived handoff.
4. Tenant and role are revalidated against the resolved Grile principal.
5. Request correlation may be propagated, but Grile still generates/validates
   its own correlation boundary.
6. A failed or unavailable identity adapter produces 401/403/503 behavior; there
   is no fallback to development headers in production.

## 3. INT-013 — shell, navigation and deep-link contract

The executable route inventory is `frontend/src/integrationContract.ts`, version
`grile-shell.v1`. It is deliberately independent of the future outer Retail
mount path.

Stable Grile child routes:

| Route key | Standalone child link | Entity | Current minimum capabilities |
|---|---|---|---|
| `overview` | `#/overview` | — | `schedule.read` |
| `program` | `#/program` | — | `schedule.read` |
| `exceptions` | `#/exceptions` | — | `schedule.read` |
| `close` | `#/close` | — | `month.close.read` |
| `jobs` | `#/jobs` | — | `jobs.read` |
| `store` | `#/store/:store_id` | stable store id | `catalog.read`, `schedule.read`, `grid.read`, `epay.read`, `sheet.read` |
| `agent` | `#/agent/:person_id` | stable person id | `schedule.read`, `grid.read`, `epay.read`, `sheet.read` |

A frontend contract test pins this inventory against the real
`requiredCapabilitiesForRoute()` map so host documentation cannot drift silently
from the mounted UI.

### 3.1 Supported mount models

A later integration may choose either:

**A. Separate Grile application behind the Retail shell**

- Retail owns the outer navigation/menu entry;
- Grile owns its child hash routes and frontend bundle;
- a reverse proxy or gateway routes Grile UI/API traffic;
- authenticated host session handoff is provided through the identity adapter.

**B. Port Grile pages/components into a shared Retail shell**

- route keys and entity/deep-link semantics remain the contract;
- Grile API/domain ownership remains unchanged;
- shared visual tokens/components may replace standalone shell components;
- backend capability/resource authorization remains authoritative.

The choice is intentionally deferred until the real integration milestone.
Neither choice permits duplicating payroll or schedule logic in Retail.

### 3.2 Deep-link rules

- Host links use stable store/person ids, never display names.
- The host owns only the outer mount path; Grile owns the child route contract.
- Unknown/unauthorized child routes fail into Grile's normal capability/access
  behavior.
- Retail must not infer authorization from whether a link is visible.
- Optional Retail→Grile links may target overview, program, store or agent.
- Optional Grile→Retail links, if later added, must use an explicit host adapter;
  arbitrary hard-coded Retail URLs do not belong in Grile domain/services.

## 4. INT-014 — exact expected Retail-side change set

The later integration milestone should require only the following bounded
Retail-side changes.

### Required

1. **One Grile navigation/mount entry** in the Retail shell or gateway.
2. **One trusted session handoff/wiring point** that allows a Retail identity
   adapter to validate the current host session and normalize it to
   `retail-grile.identity.v1`.
3. **One routing/deployment mapping** for the Grile frontend/API (or equivalent
   shared-shell mount) with no public bypass around normal authentication.
4. **Configuration only** for Grile base URL/API routing and allowed host origin
   where deployment topology requires it.

### Required only if existing Retail read contracts are insufficient

5. A narrow **read-only contract endpoint/adapter source** capable of producing
   the already-defined `retail-grile.v1` snapshot inputs. Prefer adapting existing
   Retail read/domain contracts. Do not add a Grile-specific Retail schema if
   the existing model can be translated safely outside Retail domain code.

### Optional

6. Contextual deep links from Retail store/person pages to the stable Grile
   `store`/`agent` route keys.
7. Shared visual tokens/components if the chosen shell model benefits from them.

### Explicitly not expected

The real integration must not require:

- Retail DB writes from Grile;
- Retail schema migrations for Grile payroll tables;
- Grile payroll formulas copied into Retail;
- Retail calendar/pontaj duplication;
- Google Sheets or XLSX logic in Retail;
- Retail-side worker/job orchestration for Grile;
- direct Grile imports of Retail Python/TypeScript implementation modules;
- email/name-based identity matching;
- disabling Retail or Grile authorization checks to make the mount work.

If the later integration appears to need any item in this forbidden list, stop
and amend the canonical plan/architecture before proceeding.

## 5. Integration sequence for the later milestone

The intended cutover sequence is:

1. deploy Grile independently in a non-production integration environment;
2. install/configure the trusted identity adapter;
3. prove `GET /session` role/capability behavior for representative principals;
4. prove effective store/person scope independently of host UI visibility;
5. connect the read-only `retail-grile.v1` snapshot adapter and reconcile one
   bounded cohort;
6. mount one Retail navigation entry/deep-link path;
7. run cross-stack browser and authorization tests;
8. only then consider wider server testing.

Rollback is routing/config based: remove the Retail navigation/mount/session
handoff and leave both data authorities intact. The integration must not require a
Retail data rollback merely to disable Grile.

## 6. M6 gate evidence expected

M6 can pass when the merged candidate proves:

- `FixtureRetailAdapter` and the future data adapter share the same versioned
  snapshot interface;
- domain/services have no direct Retail implementation dependency;
- unsupported data-contract versions fail closed;
- accepted generation/last-good semantics are proven;
- identity/session/capability handoff has an executable normalized contract;
- host capability claims cannot widen Grile authority;
- shell/deep-link inventory is machine-tested against the real frontend route
  capability map;
- the bounded Retail-side change set above is explicit;
- `unihub-retail` still has zero modifications from this program.
