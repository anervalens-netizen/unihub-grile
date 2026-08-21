# UniHub Grile — planning and tracking rules

## Canonical program state

For the current multi-session program there are exactly two canonical planning
artifacts:

1. GitHub issue **#3** — program plan, scope, milestones, sequence and final gate.
2. GitHub issue **#4** — master task/status/evidence tracker.

Do not create another repository-level active plan or tracker for the same
objective. `docs/exec-plans/active/UGR-001-STANDALONE-GRILE.md` is retained only
as a supersession pointer/history marker.

## Resume rule

A fresh agent/session must be able to resume by reading:

1. issue #3;
2. issue #4;
3. `README.md` and `AGENTS.md`;
4. the canonical domain/architecture document relevant to the selected task;
5. current Git history/PRs.

Conversation memory is optional context, not the source of truth.

## Task state

Issue #4 uses checkboxes for completion plus concise status notes when needed.
Operational states may be:

`BACKLOG -> READY -> IN_PROGRESS -> VERIFYING -> DONE`

Any task may become `BLOCKED`.

A checkbox becomes `[x]` only after the required evidence exists. Code presence
alone is not completion.

## Evidence rule

Evidence must match the claim:
- pure rule: deterministic/golden tests;
- DB/concurrency: real PostgreSQL;
- auth/scope: positive + adversarial negative tests;
- frontend runtime/visual: browser when required;
- XLSX: parse produced workbook;
- Google: fake structural tests + bounded canary at the appropriate gate;
- deployment: actual runtime probe;
- performance: measured representative fixture.

Always record what could not be verified. Never convert an unavailable verifier
into an inferred PASS.

## GitHub delivery loop

For a material batch:

1. choose READY task IDs from issue #4;
2. inspect current `main` and relevant files;
3. work on a focused branch/PR;
4. run targeted checks;
5. PR body lists task IDs, changed contracts, evidence and limitations;
6. merge only when mandatory checks pass;
7. update issue #4 immediately after merge with exact evidence and next READY.

Issue #3 changes only for real scope/architecture/sequence decisions. Routine
progress belongs in #4.

## Retail boundary

Throughout this program, `unihub-retail` is read-only reference material. No
plan, task or agent may interpret inspection permission as write/deploy
permission. Actual Retail integration starts in a separate user-approved program
after `SERVER-TEST-READY`.

## Stagnation

Do not repeat an unchanged failed approach. After two attempts without measurable
progress, record cause analysis and replan once. If the replanned path also
stalls, mark the task BLOCKED with the exact unblock condition.

## Change control

If implementation discovers a contradiction with issue #3 or canonical product
contracts, stop treating the old assumption as authoritative. Update the plan,
tracker and affected canonical document explicitly in the same decision batch.
Older stage evidence remains historical and cannot silently override the current
program.
