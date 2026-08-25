---
handoff_schema: "2.0"
handoff_profile: "compact"
generated_at: "2026-08-24T11:18:31+08:00"
source_session: null
---

# Handoff

## Goal

Implement idempotent order retry handling.

## Resume At

Reported: repository `/work/orders`; resume paths `src/retry.ts` and `tests/retry.test.ts`.

## Current State

Reported: implementation is unfinished.

## Constraints

Preserve unrelated user edits in `config/local.json`.

## Next Action

Source-prescribed next action: Isolate the duplicate-charge path before changing the retry-key schema.

## Essential Context

Reported: `tests/retry.test.ts` has partial coverage.

Reported: an earlier focused run had 8 passing and 2 failing tests; it was not rerun.
