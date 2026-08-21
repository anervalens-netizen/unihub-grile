# ADR-003 — Develop Grile to plugin-candidate maturity before Retail integration

Status: **ACCEPTED**  
Date: 2026-08-22

## Context

UniHub Retail is undergoing a separate development stream. UniHub Grile already
has a substantial standalone implementation, but still needs correctness,
authorization, runtime, frontend, CI, observability and integration-contract
hardening before it should be introduced into Retail.

Integrating too early would couple two moving systems, increase coordination
cost, make failures harder to isolate and risk disturbing Retail for work that
can be completed entirely inside Grile.

## Decision

Continue developing `unihub-grile` independently until it reaches the
`SERVER-TEST-READY` gate from issue #4 and an overall quality score >=8.5/10.

During this program:
- `unihub-retail` is read-only reference material;
- Grile may inspect Retail contracts/architecture/UI/auth patterns;
- all future Retail compatibility is implemented as Grile-owned interfaces,
  DTOs, adapters and fixtures;
- no Retail code, DB, service or deployment is changed;
- actual integration becomes a separate later program.

## Target integration shape

```text
Standalone development
FixtureRetailAdapter -> Grile contracts -> Grile domain/services

Later integration
RetailAdapter        -> same contracts -> same Grile domain/services
```

The final Retail integration should therefore mainly replace adapters/session
wiring and mount UI/navigation, not rewrite the Grile engine.

## Consequences

Positive:
- Grile can be changed aggressively without production/Retail risk;
- defects are easier to isolate;
- reconciliation and financial correctness can be proven independently;
- Retail integration scope becomes smaller and more predictable;
- two development streams do not block each other.

Costs:
- temporary standalone auth/runtime/frontend shell continue to exist;
- adapter contracts must be maintained explicitly;
- a final integration validation is still required because standalone proof
  cannot prove the real Retail runtime/session/deployment boundary.

## Invariants

- Domain/services do not depend on Retail implementation details.
- No direct permanent dependency on the Retail PostgreSQL schema.
- Google remains a projection/input surface, not the business database.
- Status is tracked only in issue #4.
- Old `S1…S7` stage state is historical and cannot override this decision.

## Supersedes

This ADR supersedes any interpretation of the original stage plan that requires
opening or modifying Retail as the next automatic step after the old standalone
stages.
