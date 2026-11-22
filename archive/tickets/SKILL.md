---
name: tickets
description: Manage state in a configured local Markdown or GitHub Issue ticket DAG without implementing or verifying product work.
disable-model-invocation: true
---

# Tickets

Manage one Ticket DAG through its derived `implementation-roadmap.md`. The configured Ticket source is authoritative; the roadmap never is.

Resolve the tracker before invoking the controller. An explicit `.scratch/<feature>` target selects local Markdown. Otherwise read `docs/agents/issue-tracker.md`: select GitHub only for `# Issue tracker: GitHub`; with no configured tracker, select local only when exactly one `.scratch/*/issues/` exists. Reject ambiguous or unsupported trackers.

- Local: `python3 <skill-root>/scripts/ticketctl.py <command> --feature-root <path> [--ticket <ticket>]`.
- GitHub: `python3 <skill-root>/scripts/ticketctl.py <command> --tracker github --repo-root <repo> [--repo <owner/name>] [--feature <parent>] [--ticket <ticket>]`; read [github-tracker.md](references/github-tracker.md).

Map public `[ticket]` to `--ticket`. If invocation omits a mode, use `status`; reject unknown modes. Consume only [the versioned output contract](references/output-contract.md).

## Modes

- `status`: inspect the Feature without mutation.
- `next [ticket]`: resume the requested eligible paused ticket or activate a frontier ticket. With no ticket, prefer the oldest resumable ticket, then the oldest frontier ticket. Return the Work Packet and stop; do not implement it.
- `done [ticket] [--note <note>]`: record the user's explicit completion decision for the active ticket. Do not inspect code, run tests, consume implementation output, or claim verification.
- `pause [ticket] --reason <reason> [--wait-for <ticket>] [--handoff-file <path>]`: pause active work. `--wait-for` adds runtime remediation dependencies and may repeat. Targets and handoffs must already exist; creation belongs to other skills.

## Recovery

Read [references/recovery-contract.md](references/recovery-contract.md) for pause/resume, handoffs, `DEADLOCK`, or mutation retries.

## State Rules

Maintain one active ticket. Only explicit `done` creates new completion state. Preserve legacy completion and confirmation history without treating it as current verification. Published blockers and paused-ticket remediation edges must remain acyclic; never make remediation depend on its paused parent.

Limit this Skill to controller-managed ticket and roadmap state. Never invoke `$implement` even when output recommends it. Do not implement, verify, create tickets or handoffs, commit, push, deploy, or open pull requests.
