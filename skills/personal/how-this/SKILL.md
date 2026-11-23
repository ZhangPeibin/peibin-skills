---
name: how-this
description: Review the proposed implementation approach for a ticket before coding. Explain why this code and design fit, scan the existing codebase for pieces that should be reused instead of duplicated, compare plausible better architectures, and record an approach verdict on the ticket. Use after tickets exist and before implementation begins.
disable-model-invocation: true
argument-hint: "<ticket reference or planned change>"
---

# How This

An approach gate. Run it once a ticket exists and before implementation starts. Confirm that the planned implementation fits the ticket, reuses what the codebase already has, and is the best available design — then record the verdict on the ticket.

This skill recommends; it does not implement. Do not edit source, create branches or commits, or refactor. The only write is the review comment.

## Resolve the target

Take the ticket reference, planned change, or affected area. Read the ticket, its spec and discussion, `docs/adr/`, `CONTEXT.md`, applicable `AGENTS.md`, and the surrounding code. Follow the configured tracker conventions and repository workflow. If the target is ambiguous, ask one focused question.

Trace the affected path from entry to observable result. Identify the modules, contracts, state, async work, and tests the change touches.

## Reuse and duplication scan

This step is mandatory. Before judging the approach, search the codebase for what already exists:

- services, helpers, utilities, and components that perform the same job;
- existing schemas, models, flags, and configuration;
- earlier tickets, branches, or PRs that touched the same area;
- established patterns the new code should follow.

List each reuse candidate with `file:line` and say whether the plan should call it, extend it, or explain why it does not fit. Flag concrete duplication, not speculation.

## Evaluate the approach

For the proposed approach, state the design in one paragraph, then evaluate:

- which constraints and existing patterns force this choice;
- at least one plausible alternative or better architecture, and the trade-offs against it;
- reversibility, blast radius, and effect on tests and boundaries;
- fit with ADRs and the domain model, including deliberate deviations that must not be "fixed".

Distinguish reasons recorded in tickets, PRs, ADRs, or git history from your own inference. If the plan has no stated approach, propose one.

## Verdict

Choose exactly one:

- **adopt** — sound as proposed.
- **adjust** — close, with specific changes.
- **reuse-existing** — the codebase already provides the mechanism; use it.
- **need-spike** — a bounded investigation is required before committing.
- **reconsider** — a materially better design exists.

Give a concrete next step for each.

## Persist the verdict

Append `## Approach Review — <YYYY-MM-DD>` to the ticket, or to the PR when one exists, following tracker conventions. If a prior review exists, add a new dated section and note what changed. Keep it short:

```markdown
## Approach Review — <YYYY-MM-DD>

**Verdict:** adopt | adjust | reuse-existing | need-spike | reconsider
**Proposed approach:**
**Reuse candidates:**
**Duplication to avoid:**
**Better alternative (if any):**
**Trade-offs and risks:**
**Next step:**
```

Finish by telling the user the verdict and the next action.
