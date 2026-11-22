# GitHub Tracker

Use this backend only when the repository config selects GitHub. `status` is read-only. Mutating commands may perform only the state transitions described here; authentication changes, Issue creation, implementation, verification, pushes, deployment, and PR mutations remain forbidden.

## Feature DAG

The parent Feature is an Issue. Native sub-issues and blocked-by dependencies take precedence. Use child-body `## Parent` and `## Blocked by` only when the native relationship is empty or unavailable; body membership also requires an author with triage, write, maintain, or admin permission. Without `--feature`, discover both sources and select the sole parent with open children, or the sole historical parent. Reject ambiguity.

An open, unassigned child labelled `ready-for-agent` is ready. Any assignment occupies the single active lane; only a current assignee may control it. A valid controller pause on an unassigned Issue is paused. An unassigned Issue without the label or a pause is non-executable. A closed Issue is Done. Managed comments require author permission at triage, write, maintain, or admin; other comments are untrusted.

## State Changes

- `next` assigns the selected Issue to `@me`, checks the shared Feature for a conflicting assignment, and rolls back a losing claim. A resume appends cleared controller state only after the claim succeeds.
- `pause` appends versioned state with the reason, remediation waits, and optional handoff, then removes `@me`.
- `done` appends explicit completion state and then closes the Issue. `$implement` never closes it.

Managed comments contain the same versioned state block used by local tickets. Exact retries must not duplicate a comment and must finish any remaining assignment, state-comment, cleanup, or close operation.

## Roadmap

GitHub Issue `<parent>` derives `.scratch/github-<parent>/implementation-roadmap.md`. `status` reports `MISSING`, `CURRENT`, `STALE`, or `UNREADABLE` without creating it. `next`, `pause`, and `done` synchronize it. Links point to authoritative Issue URLs; the local roadmap remains disposable derived data.
