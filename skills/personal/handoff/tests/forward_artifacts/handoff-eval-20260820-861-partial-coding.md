---
handoff_schema: "1.0"
generated_at: "2026-08-20T19:23:47+08:00"
source_session: null
---

# Handoff

## Goal And Definition Of Done

Implement idempotent order retry handling so retries cannot create duplicate charges. Completion requires focused retry coverage, including the duplicate-charge case, to pass.

## Resume Context

[?] Repository: `/work/orders` (reported by the trusted runtime context; unavailable during handoff generation).

[?] Working directory: not supplied.

## Current State

### Completed

None

### In Progress

[?] Idempotent retry handling remains under investigation.

### Remaining

[?] Implement and validate the retry handling.

## Decisions And Rationale

None

## Constraints And Assumptions

**Constraints**

Preserve unrelated user edits in `config/local.json`.

**Assumptions**

None

## Open Questions

Can the user provide the evidence or input specified in Continuation Payload > Debugging?

## Evidence And References

Evidence labels: `[V]` was verified while generating this handoff; `[?]` was remembered, inferred, or not rechecked. Drift-prone state must be rechecked before acting.

[?] An earlier focused run reportedly had 8 passing and 2 failing tests; it was not rerun.

## Risks And Blockers

**Risks**

The reported state may be stale, which could lead to diagnosing the wrong retry path.

**Active Blockers**

None

## Next Action

Ask the user to answer Open Questions. Priority: highest. Expected continuation result: obtain the current diagnostic evidence needed to isolate the duplicate-charge path before considering a retry-key schema change.

## Continuation Payload

### Coding

[?] Reported implementation entry point: `src/retry.ts`.

[?] Reported test contract: `tests/retry.test.ts` has partial retry coverage.

### Debugging

[?] Symptom: an idempotent retry can reach a duplicate-charge path. Expected behavior: see Goal And Definition Of Done.

[?] Leading hypothesis: a charge can be issued before an existing retry key is recognized. A schema mismatch remains unverified.

Needed from the user: current access to the reported repository, or a fresh focused failure report containing failing test names, assertions, stack traces, and the retry/order/charge event sequence.

If the charge occurs without a prior retry-key lookup or hit, the duplicate-charge path is independent of the schema. If lookup occurs but fails to match the same retry identity, that indicates a schema identity mismatch.
