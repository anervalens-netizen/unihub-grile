# UniHub Grile documentation index

## Current program

- **Plan:** GitHub issue #3 — `PROGRAM PLAN — UniHub Grile 8.5+ Standalone Plugin Candidate`
- **Tracker:** GitHub issue #4 — `MASTER TRACKER — UniHub Grile to Server-Test Ready`

These two issues control scope and progress. No Markdown tracker in this directory
overrides them.

## Canonical documents

| Document | Authority |
|---|---|
| `../README.md` | project mission, current strategy and entry point |
| `../ARCHITECTURE.md` | architecture, data authority and boundaries |
| `PRODUCT_CONTRACT.md` | product/business behavior |
| `MOBIUP_RULE_PACK.md` | Mobiup payroll/Pontaj formulas and compatibility |
| `UX_EXCEL_SPEC.md` | frontend UX, Google Sheet and XLSX contract |
| `RETAIL_INTEGRATION_CONTRACT.md` | future Retail adapter/session/data contract |
| `QUALITY_GATES.md` | evidence, scoring and server-test-ready gate |
| `../AGENTS.md` | agent execution/governance rules |
| `operations/local-commands.md` | current local development/verification workflow |
| `operations/google-provider-config.md` | Google provider selection, secrets and live-mutation gates |

## Historical material

`exec-plans/active/UGR-001-STANDALONE-GRILE.md` is intentionally a supersession
pointer. The former S1–S7 plan is not an active tracker.

Files under `decisions/` may include historical stage terminology. Their durable
decisions remain useful unless superseded, but they do not represent current
progress.

`.agent/evidence/` and Git history may contain exact-commit verification evidence
from earlier development. Evidence remains evidence for the artifact it tested;
it does not automatically certify current `main`.

## Conflict rule

If documentation conflicts, use this order:

1. issue #3;
2. issue #4 for status;
3. `PRODUCT_CONTRACT.md` for business behavior;
4. `ARCHITECTURE.md` for architecture;
5. specialized canonical document for its own domain;
6. ADRs;
7. historical evidence.

Fix discovered conflicts in the same development batch rather than adding a new
parallel source of truth.
