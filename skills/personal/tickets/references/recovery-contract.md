# Recovery And State Contract

Read this reference for pause/resume, handoffs, `DEADLOCK`, or mutation retries.

## Ticket handoff

This Skill only binds an existing schema 2.0 handoff under `<feature-root>/handoffs/`; it never generates one. The handoff is recovery context, not completion evidence.

A remediation pause becomes automatically resumable after every `--wait-for` ticket is Done. A handoff alone never makes a Ticket automatically resumable. Any paused Ticket whose dependencies are Done may be resumed with explicit `next <ticket>`. A pause without an executable alternative yields `DEADLOCK`; unexpected interruption leaves the Ticket active, so repeated `next` recovers it.

## Remediation

`pause --wait-for 05` adds runtime edge `05 -> paused-ticket`. Ticket 05 must exist and not depend on its parent. Completion makes the parent resumable. The legacy `--until` spelling remains accepted but is not public. Pause reasons contain 1 to 1,000 characters.

## Idempotence

Repeated `next` is read-stable. Exact explicit-ticket `pause` and `done` retries return existing state, including after later activation; a different payload is a new transition and is rejected after pause or completion. After `POST_COMMIT_ERROR`, retry the exact command before another action.
