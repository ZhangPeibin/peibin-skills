---
name: to-spec
description: Turn the current conversation into a spec and publish it to the project issue tracker — no interview, just synthesis of what you've already discussed.
disable-model-invocation: true
---

This skill takes the current conversation context and codebase understanding and produces a spec (you may know this document as a PRD). Do NOT interview the user — just synthesize what you already know.

The issue tracker and triage label vocabulary should have been provided to you — run `/setup-engineering-workflow` if not.

## Process

1. Explore the repo to understand the current state of the codebase, if you haven't already. Read the supplied requirements discussion, including decisions and open questions. Use the project's domain glossary vocabulary throughout the spec, and respect any ADRs in the area you're touching.

2. Sketch out the seams at which you're going to test the feature. Existing seams should be preferred to new ones. Use the highest seam possible. If new seams are needed, propose them at the highest point you can. The fewer seams across the codebase, the better - the ideal number is one.

Check with the user that these seams match their expectations.

3. Write the spec using the template below, then publish it to the project issue tracker. Omit optional design sections when they add no useful decision context. Preserve uncertainty rather than inventing agreement: identify questions that block affected implementation and the next evidence or decision needed. Apply the `ready-for-agent` triage label only when no unresolved question blocks implementation; otherwise use the configured tracker convention for unresolved work.

<spec-template>

Keep the opening concise enough to show the problem, expected outcome, and any material unresolved blocker before the detailed stories. Briefly flag blockers here and keep their options and next steps in Open Design Questions.

## Problem Statement

The problem that the user is facing, from the user's perspective.

## Solution

The solution to the problem, from the user's perspective.

## User Stories

A numbered list of distinct user stories sufficient to cover the agreed scope. Each user story should be in the format of:

1. As an <actor>, I want a <feature>, so that <benefit>

<user-story-example>
1. As a mobile bank customer, I want to see balance on my accounts, so that I can make better informed decisions about my spending
</user-story-example>

Cover meaningful scenarios and failure behavior without padding the list with redundant variations.

## Success and Acceptance Criteria

Describe observable outcomes for users or operators and how to determine whether they have been achieved. Component or technology choices alone are not success criteria. Include quantitative targets only when grounded in agreed requirements or evidence; do not invent performance thresholds.

For example, replace "Add memory and CPU collectors" with "After a manual app session, the operator can correlate marked actions with memory and CPU changes, and distinguish missing samples from measured zero values." Use only outcomes supported by the actual requirements.

## Implementation Decisions

A list of material implementation decisions and their rationale; omit this section when none are needed. Prioritize decisions by the cost of getting them wrong, such as persistent data models, system boundaries, interface compatibility, migrations, and authorization. Leave cheap, reversible details to implementation. This can include:

- The modules that will be built/modified
- The interfaces of those modules that will be modified
- Technical clarifications from the developer
- Architectural decisions
- Schema changes
- API contracts
- Specific interactions

Do NOT include specific file paths or code snippets. They may end up being outdated very quickly.

Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it within the relevant decision and note briefly that it came from a prototype. Trim to the decision-rich parts — not a working demo, just the important bits.

## Alternatives Considered (optional)

Briefly describe credible alternatives to important decisions and why they were rejected. Include only alternatives actually discussed or investigated; do not manufacture a comparison to fill the section.

## Open Design Questions (optional)

For each unresolved design question, state the problem, plausible options, the next step to resolve it, and which implementation work it blocks. When evidence is missing, propose a bounded experiment with a concrete output. Resolve high-cost decisions before affected implementation begins; unrelated work need not wait.

## Testing Decisions

A list of testing decisions that were made. Include:

- A description of what makes a good test (only test external behavior, not implementation details)
- Which modules will be tested
- Prior art for the tests (i.e. similar types of tests in the codebase)

## Out of Scope

A description of the things that are out of scope for this spec.

## Further Notes

Any further notes about the feature.

</spec-template>
