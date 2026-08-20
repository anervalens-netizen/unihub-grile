# ExecPlan rules

Use one ExecPlan for substantial, multi-session, multi-component work. Keep one
canonical active plan per objective and update it as the living source of truth.

## Required properties

- A fresh session must be able to resume from the repository, plan, evidence
  pointers, and Git history without the original conversation.
- Record baseline, objective, non-goals, dependencies, acceptance criteria,
  progress, attempts, decisions, evidence, risks, and next exact step.
- Every acceptance criterion starts `UNVERIFIED`.
- Update after each material attempt, discovery, failure, decision, commit, audit
  verdict, integration, or deployment event.

## State model

Task states: `BACKLOG -> READY -> BUILDING -> VERIFYING -> PASS`. Any state may
become `BLOCKED`. An audit `FAIL` returns the task to `BUILDING` and increments
attempts.

Overall outcomes: `DONE`, `PARTIAL`, or `BLOCKED`. `DONE` requires all criteria
to be `PASS`, relevant regression checks, and required runtime proof.

## GO/NO-GO rule

- The builder never self-authorizes `PASS`.
- The builder provides the exact commit plus listed evidence.
- A later read-only reviewer checks the contract against that artifact and issues
  `GO` (`PASS`), `NO-GO` (`FAIL`), or `BLOCKED`.
- The primary reviewer records the verdict in the plan.

## Evidence hygiene

Store concise commands, hashes, counts, and sanitized artifacts. Never store
credentials, Google Sheet IDs, personal data, production rows, or unnecessary
raw logs.

## Stagnation

Never repeat an unchanged failed approach. After two attempts without measurable
progress, record cause analysis and replan once. If the replanned approach also
stalls, mark `BLOCKED` with the exact unblock condition.

