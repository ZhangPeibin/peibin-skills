# Recovery And State Contract

Read this reference for pause/resume, handoffs, `DEADLOCK`, or mutation retries.

## Ticket handoff

This Skill only binds an existing schema 2.0 handoff under `<feature-root>/handoffs/`; it never generates one. The handoff is recovery context, not confirmation evidence.

A handoff pause is resumable; a remediation pause waits for every `--until` ticket. A pause with neither yields `DEADLOCK` when nothing else is executable and requires explicit `next <ticket>`. Unexpected interruption leaves the ticket active, so repeated `next` recovers it.

## Remediation

`pause --until 05` adds runtime edge `05 -> paused-ticket`. Ticket 05 must exist and not depend on its parent. Completion makes the parent resumable; it still requires fresh confirmation. Pause reasons contain 1 to 1,000 characters.

## Idempotence

Repeated `next` is read-stable. Exact explicit-ticket mutation retries return existing state, including after later activation; a different payload is a new transition and is rejected after pause or completion. After `POST_COMMIT_ERROR`, retry the exact command before another action.
