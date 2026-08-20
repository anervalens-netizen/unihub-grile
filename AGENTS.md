# UniHub Grile

Standalone, optional workforce-grid application. It must remain deployable and
testable without importing or modifying UniHub Retail.

## Start here

1. Read `README.md`, `ARCHITECTURE.md`, `docs/PRODUCT_CONTRACT.md`, and
   `docs/UX_EXCEL_SPEC.md`.
2. Read the single active plan under `docs/exec-plans/active/`.
3. Recheck Git status and preserve all newer/user work.
4. Work only on the current stage. Update the active plan after every material
   result, decision, failure, commit, or audit verdict.

## Delivery rules

- The active ExecPlan is the canonical tracker. Do not create parallel trackers,
  handoff files, or transcript summaries.
- Acceptance criteria start `UNVERIFIED`. A stage becomes `PASS` only after a
  read-only GO/NO-GO audit against the exact commit and listed evidence.
- Stages are deliberately large. Do not split them into ceremonial PRs or tiny
  commits. Prefer one coherent, revertible commit per completed stage.
- Sequential development on `main` is allowed while one writer is active. If
  multiple agents write concurrently, use separate branches/worktrees and give
  each agent non-overlapping ownership.
- Run targeted local checks before any CI. Do not use CI as an iterative test
  runner.
- Never commit credentials, Google Sheet IDs, personal data, production rows, or
  screenshots containing employee information.

## Protected boundaries

- `/opt/Mobiup/unihub-retail` is read-only until Stage 7 is explicitly opened.
- `/opt/Mobiup/grile-salarii` is a legacy/reference repository. Do not mutate its
  permanent Sheet registry, templates, outputs, or live Google files.
- V1 and the current Retail V2 pilot remain unchanged until shadow reconciliation
  has passed and migration is explicitly approved.
- No direct long-term dependency on the Retail PostgreSQL schema. Development
  uses fixtures; final integration uses a versioned connector contract.
- Google Sheets are projections plus two bounded E-pay inputs, never the business
  database or calculation engine.
- Financial totals, attendance, sales attribution, month close, and reopen actions
  require deterministic tests and append-only audit evidence.

## Product invariants

- Exactly one working agent per store and business date.
- At most one working store per agent and business date.
- The calendar is authoritative; attendance is derived from it and changes with it
  until the month is closed.
- The entire store-day sale is credited to the calendar-assigned agent.
- A sale is counted once in company/store totals even when personal credit changes.
- `EXTRA_HOME` changes the day classification/compensation, not the physical sale.
- Agents do not edit schedules or grids. In Google Sheets, only the two E-pay
  quantity cells per agent are editable.
- A closed month is immutable. Reopen is admin-only, requires a reason, and is
  fully audited.

## Default stack and verification

- Backend: Python, FastAPI, PostgreSQL, explicit service/repository boundaries.
- Frontend: React, TypeScript, Vite.
- Async work: one durable worker process with typed jobs; web requests never wait
  on Google I/O.
- Tests: deterministic domain/unit tests first, then repository/API tests, then a
  small real-browser and Google canary gate.
- Exports/imports must be tested by parsing the produced workbook, not only by
  checking that a file exists.

