---
name: run-ticket-plan
description: Control a published Local Markdown ticket plan without implementing product code.
disable-model-invocation: true
---

# Run Ticket Plan

Operate the ticket plan as a control plane. Treat the canonical spec and frozen ticket contracts as authoritative, the formal ticket Status as execution-state authority, and the roadmap as a derived view. Leave product implementation to an independent Implementation Agent or developer.

## Resolve the invocation

Map the primary workflow to one mode:

When an explicit invocation omits the mode, run `status`; never infer a mutating mode. Reject every other unknown mode.

- `status`: validate and report state without changing any file.
- `next [ticket]`: activate exactly one frontier ticket, persist its Verification Plan, synchronize or generate the roadmap, and return a Work Packet.
- `confirm [ticket]`: verify independently implemented work and persist PASS, REMEDIATE, or BLOCKED state.

Keep these explicit administrative actions outside the primary workflow:

- `reconcile <ticket> --reason <operator-reason>`: explicitly migrate a legacy or invalid Done ticket to In Progress.
- `record-external <ticket> <check> --result PASS --reference <stable-reference> --issuer <independent-issuer>`: record operator-supplied independent PASS evidence for a pending non-blocking external checkpoint.

For `reconcile`, require a non-empty reason supplied by the operator and pass it through unchanged; never author or substitute the reason. After the controller returns the recovery handoff for `Done -> In Progress`, report it and end this administrative action. Run `plan-check`, `verify`, or `complete` only when separately requested.

For `record-external`, require every displayed input and invoke the controller with `--result PASS`. Stop and identify any missing input; never infer evidence or accept the implementing or control-plane agent as issuer. Use `--repair` only after the controller reports the exact repair command and the user explicitly authorizes that repair.

Return a concise migration message for removed `run` and `continue` requests: direct the user to `status`, `next`, or `confirm`. Do not retain an implementation alias.

Require an explicit feature root, or discover it only when exactly one `.scratch/*/issues/` directory exists. Stop on ambiguity. Use `python3 <skill-root>/scripts/ticketctl.py <command> --feature-root <path>` for deterministic operations. Read [references/result-contract.md](references/result-contract.md) only when diagnosing a contract error or using a recovery primitive; normal `status`, `next`, and `confirm` rely on controller-enforced contracts.

## Establish authority

Apply the user's mode, feature root, and optional ticket first. Resolve the repository root without mutation and read its applicable `AGENTS.md` before invoking the controller. Let the controller parse the tracker convention, optional `.ticketctl.json`, canonical spec identity, all formal tickets, DAG, baseline, and roadmap. Do not duplicate that deterministic work in model context.

For `next`, run the internal read-only `prepare-next` command first, then read any additional nested `AGENTS.md` applicable to the returned ticket path. The Planning Packet is the sole source of product requirements. Use applicable repository instructions for operating constraints and repository-approved verification commands and paths; never use them to add or reinterpret product requirements. Do not read the canonical spec body, raw ticket files, full roadmap, full result contract, or product code during a normal `next`.

During `confirm`, read implementation artifacts only through the persisted Verification Plan and safe verification engine.

## Report status

Run `status`. Report the active ticket, all ticket statuses, complete frontier, recommended next ticket, Verification Plan state, roadmap state, pending external checkpoints, tracker baseline, and fatal/migratable/informational drift.

Keep `status` strictly read-only. Never repair drift, create a roadmap, claim a ticket, or infer Done from code or evidence.

## Activate next

Follow this sequence:

1. Run `ticketctl.py prepare-next --feature-root <path> [--ticket <id>]`. This internal command is read-only and returns the selected frontier or interrupted active ticket, exact frozen requirements, plan-generation constraints, compact baseline/spec identities, and roadmap state. Stop on any error.
2. If `planning_required` is false, do not regenerate the existing valid plan. Invoke `next` for the same ticket without `--plan-file` to activate an eligible Open ticket or finish an interrupted activation and idempotent roadmap synchronization.
3. Otherwise generate one Verification Plan from packet requirements and verification mechanics grounded in the packet or applicable repository instructions. Map every returned requirement id and obey the embedded `plan_contract`, including exact `source` and `requirement` text, blocking ids, external/manual rules, safe direct argv, and repository-contained paths.
4. If any blocking requirement lacks a non-invented safe check, stop before mutating `next` and report `CONTRACT_NOT_PLANABLE`. Return the incomplete or contradictory contract to the ticket publication workflow; do not guess commands, paths, evidence, or requirements.
5. Do not prescribe classes, packages, architecture, algorithms, functions, or implementation strategy unless the frozen ticket explicitly requires them.
6. Put the JSON object in a temporary file outside the repository and invoke `ticketctl.py next --feature-root <path> --ticket <packet-ticket> --plan-file <temporary-json>`. Always remove the temporary input in a cleanup/finally path, including when `next` fails. Never persist the Planning Packet or any cache.
7. Let `next` independently recheck the complete baseline, DAG, lane, ticket contract, and Plan under its mutation lock. Treat any change since `prepare-next` as a fresh validation failure; never force stale packet output through activation.
8. Read the emitted Work Packet. Confirm Status is In Progress, the plan hash and check ids match, and the roadmap is current.
9. Return the Work Packet and end the `next` branch. Product implementation begins only in a separate user-authorized agent or developer turn; do not invoke an implementation skill, coding subagent, `$tdd`, `$implement`, or `/implement` in this branch.

Reuse an already valid Plan on an eligible Open ticket. Allow an idempotent `next` rerun to finish the same In Progress activation when plan or roadmap persistence was interrupted. Reject invalid or conflicting existing plans; never activate a second lane or overwrite a different plan.

## Confirm work

Run `ticketctl.py confirm --feature-root <path> [--ticket <id>]` only after the independent implementer reports readiness.

Execute command-backed checks only in the controller's disposable repository snapshot under a supported OS sandbox. Never run them in the real repository root. Treat an unavailable secure sandbox or any source/tracker mutation inside the snapshot as `BLOCKED`; discard the snapshot and never roll changes back into the real worktree.

- On `PASS`, require a valid Result and receipt, Status Done, roadmap synchronization, pending external checkpoints, and recalculated frontier.
- On `REMEDIATE`, leave Status In Progress and end with failed checks plus the remediation handoff. Product changes belong to a separate implementation turn; do not edit source, add tests, retry with changed implementation, or repair failures.
- On `BLOCKED`, retain Status Blocked and return each exact unlocking condition. Do not synthesize independent evidence. After the condition is met, use the explicit `resume` recovery primitive, then confirm again.

Preserve all prior attempts, Comments, unrelated changes, frozen contract content, completion receipts, and external evidence.

## Maintain derived roadmap

Let `next` create `<feature-root>/implementation-roadmap.md` when no roadmap exists. Generate exactly one ticket card per local Core ticket and one marked Current Frontier block. Derive cards only from formal tickets and the Verification Plan.

Treat tracked and untracked roadmaps identically. Never require a roadmap at the committed tracker baseline, include it in source revision or completion integrity, or let it affect the DAG. Synchronize only checkbox state and the Current Frontier marker block in an existing valid roadmap. Use `sync-roadmap --regenerate` only as an explicit repair for a malformed derived view.

## Use recovery primitives

Keep `prepare-next` as the internal planning primitive. Keep `validate` and `frontier` as diagnostic primitives, and `claim`, `plan-check`, `verify`, `complete`, `resume`, and `sync-roadmap` as explicit recovery or compatibility commands. Do not present these helpers or `claim -> implement -> verify` as the primary workflow.

Require independently issued evidence for external/manual checks. Preserve Core and external evidence as separate fault domains. Limit this Skill's mutations to controller-managed ticket and roadmap state. Handle commits, pushes, pull requests, deployment, publication, live-system writes, testnet actions, and real-funds operations only through a separate explicitly authorized workflow.

## Report the outcome

Report only fields applicable to the selected mode and emitted by the controller. Include the mode, selected ticket when any, status or result, and relevant drift, roadmap state, and recalculated frontier when available. Keep machine-readable state in the formal ticket; never replace it with a prose-only summary.
