# UGR-001 — historical standalone-stage plan

Status: **SUPERSEDED — DO NOT USE FOR CURRENT EXECUTION**  
Superseded on: 2026-08-22

The original `UGR-001-STANDALONE-GRILE` stage plan (`S1…S7`) was the execution
ledger used to bootstrap the repository and prove the first domain/backend/UI
capabilities.

It is no longer the source of truth because the project direction is now:

> Develop UniHub Grile independently to a >=8.5/10 standalone plugin candidate,
> ready for server testing, while keeping UniHub Retail completely untouched
> until a later explicit integration program.

## Current canonical sources

1. Program plan: https://github.com/anervalens-netizen/unihub-grile/issues/3
2. Master tracker: https://github.com/anervalens-netizen/unihub-grile/issues/4
3. `README.md`
4. `ARCHITECTURE.md`
5. `docs/PRODUCT_CONTRACT.md`
6. `docs/QUALITY_GATES.md`
7. `AGENTS.md`

## Historical evidence

Commits, audit notes and acceptance evidence produced while this file was active
remain valid **as historical evidence for the exact artifacts they tested**.
They do not mean that the current `main` automatically satisfies the new
program's server-test-ready gate.

The old stage statuses must not be translated into current completion. In
particular:

- `S1…S7` labels do not drive task selection;
- earlier PASS applies only to the exact tested contract/commit;
- new audit findings and the new quality target can reopen/harden previously
  implemented areas;
- actual Retail integration remains outside the current program.

Do not add progress to this file. Update issue #4 instead.
