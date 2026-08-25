---
handoff_schema: "2.0"
handoff_profile: "compact"
generated_at: "2026-08-21T16:40:27+08:00"
source_session: null
---

# Handoff

## Goal

Implement idempotent order retry handling.

## Resume At

Reported: Repository `/work/orders`; cwd, branch, and HEAD were not supplied.

Reported: WIP locations are `src/retry.ts` and `tests/retry.test.ts`.

## Current State

Reported: The implementation is unfinished and test coverage is partial.

## Constraints

Preserve unrelated user edits in `config/local.json`.

## Next Action

Source-prescribed next action: Continue by isolating the duplicate-charge path before changing the retry-key schema.

## Essential Context

Reported: An earlier focused run reported 8 passing and 2 failing tests, but it was not rerun.
