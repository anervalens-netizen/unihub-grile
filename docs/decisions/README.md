# Architectural and product decisions

This folder records durable decisions that explain *why* the current design is
what it is. ADRs are not trackers and do not carry task status.

## Authority

Current authority order:

1. Program plan — GitHub issue #3.
2. Master Tracker — GitHub issue #4 for status/evidence.
3. Canonical product/architecture documents.
4. Accepted ADRs in this folder.
5. Historical stage decisions/evidence.

An ADR may refine architecture, but it cannot silently contradict issue #3 or the
canonical product contract. If a new decision changes those sources, update them
in the same batch.

## Current decisions

- `adr-003-standalone-plugin-candidate.md` — current strategic decision: finish
  Grile independently to server-test-ready quality while Retail remains read-only.

## Historical decisions

- `s1-stack.md` — historical bootstrap stack, initial enforcement and auth skeleton.
- `s2-calendar.md` — historical calendar/revision decisions.

Historical documents remain useful evidence/context, but stage-specific language
inside them does not control current execution.

## ADR format

A new durable decision should include:
- status/date;
- context;
- decision;
- consequences;
- invariants/boundaries;
- superseded documents/decisions if any.

Routine implementation details belong in PRs and issue #4, not new ADRs.
