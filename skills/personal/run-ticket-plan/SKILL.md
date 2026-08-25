---
name: run-ticket-plan
description: Advance a local to-tickets Markdown DAG with status, next, confirm, pass, and pause while leaving product implementation to a separate workflow.
disable-model-invocation: true
---

# Run Ticket Plan

Control tickets under `.scratch/<feature>/issues/`; ticket files are authoritative and `implementation-roadmap.md` is derived.

Use `python3 <skill-root>/scripts/ticketctl.py <command> --feature-root <path> [--ticket <ticket>]`; map public `[ticket]` to `--ticket`. Require an explicit feature root, or discover it only when exactly one `.scratch/*/issues/` exists. If invocation omits a mode, use `status`; reject unknown modes.

Consume only the versioned fields in [references/output-contract.md](references/output-contract.md).

## Modes

- `status`: inspect the Feature without mutation.
- `next [ticket]`: resume the requested eligible paused ticket or activate a frontier ticket. With no ticket, prefer the oldest resumable ticket, then the oldest frontier ticket. Return the Work Packet and stop; do not implement it.
- `confirm [ticket]`: independently verify every acceptance criterion. Mark Done only when all criteria pass.
- `pass [ticket] [--note ...]`: record manual confirmation and mark Done without verification. Invoke only when the user explicitly requests `pass`; never infer it from failed or blocked checks.
- `pause [ticket] --reason <reason> [--until <ticket>] [--handoff-file <path>]`: pause active work. `--until` adds runtime remediation dependencies and may repeat. The target ticket must already exist; ticket creation belongs to `to-tickets`.

## Confirm

1. Run `prepare-confirm`, then inspect the implementation under applicable repository instructions without modifying product code or tests.
2. Gather current, direct evidence for every criterion. Use `PASS` only for demonstrated behavior, `FAIL` for mismatch, and `BLOCKED` when decisive evidence needs unavailable or unsafe access.
3. Write [references/result-contract.md](references/result-contract.md) version 1 to a temporary file outside the repository, submit it with `confirm --result-file`, and remove it. Stop after any non-PASS result.

## Recovery

Read [references/recovery-contract.md](references/recovery-contract.md) for pause/resume, handoffs, `DEADLOCK`, or mutation retries.

## State Rules

Maintain one active ticket. `confirm` and explicit `pass` are the only completion paths. `Blocked by` and paused-ticket remediation edges must remain acyclic; never make remediation depend on its paused parent.

Limit this Skill to controller-managed ticket and roadmap state. Do not implement work, create remediation tickets, add tracker requirements, commit, push, deploy, or open pull requests.
