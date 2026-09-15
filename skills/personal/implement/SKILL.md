---
name: implement
description: "Implement a piece of work based on a spec or set of tickets."
disable-model-invocation: true
---

Implement the work described by the user in the spec or tickets.

Before changing files, read and follow `docs/agents/worktrees.md` when it exists. If it requires an isolated worktree, create or reuse it first and do all remaining work there.

Use /tdd where possible, at pre-agreed seams.

Follow the repository's required verification gates. Choose focused checks for the changed behavior and affected risks, reuse still-applicable results, and broaden only when the change, repository gates, or an explicit request requires it. Do not run tests on a fixed cadence or require a full suite merely because implementation is ending. Report actual outcomes and relevant checks not run.

Once done, use `code-review` for the implementation review when available; otherwise review the scoped diff and affected behavior directly. Reuse an applicable review already completed for this change. Reserve `to-audit` for an explicitly requested deep audit; do not automatically perform both.

Commit your work to the current branch.
