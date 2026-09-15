---
name: requirements-analysis
description: Clarify a feature before its PRD by exploring repository facts, interviewing the user one decision at a time, and maintaining a durable pre-PRD discussion document at the repository-configured location. Use when a feature request is still ambiguous, when product requirements need stress-testing, or when the reasoning and decisions leading to a later spec must survive across sessions.
---

# Requirements Analysis

Turn an unclear feature request into a durable, PRD-ready discussion record. Own the analysis and `discussion.md`; do not write the PRD, create issues or tickets, or implement the feature.

## 1. Locate the feature workspace

Resolve the discussion path in this order:

1. Use a path the user supplied.
2. Follow `docs/agents/feature-docs.md` when it exists.
3. Follow a feature-document convention in `AGENTS.md` or `CLAUDE.md`.

If none resolves the path, stop before writing and tell the user to run `$setup-engineering-workflow`. Do not invent or persist a repository-level directory convention from this skill.

Treat the release assignment as a user decision. Derive a short kebab-case feature slug from the feature name and state it when proposing the path. Ask only for information that cannot be discovered from the repository or conversation.

If the discussion file already exists, read it completely and resume it without discarding prior decisions or session history. Never overwrite an existing `prd.md`.

## 2. Establish repository context

Read the repository instructions and the code relevant to the request. Also read applicable domain glossaries and ADRs. Resolve facts by inspecting the environment; ask the user only for product decisions.

Capture the initial request in `discussion.md` before the interview grows long. Use this structure, omitting only sections that genuinely do not apply:

```markdown
# <Feature name> - Requirements Discussion

**Status:** analyzing
**Release:** <release>
**Feature:** <feature-slug>

## Problem

## Users and Outcomes

## Current Experience

## Scope

### In Scope

### Out of Scope

## Required Behaviour

## Edge and Failure Cases

## Constraints and Dependencies

## Decisions

## Open Questions

## Evidence

## Session Log
```

Keep this a structured analysis record, not a verbatim chat transcript. Record the rationale for material decisions and retain rejected alternatives only when they explain an important boundary.

For each open question, record the problem, plausible options, and the next step needed to resolve it. State whether it blocks the PRD or can be carried into technical design.

## 3. Interview one decision at a time

Use the `grilling` protocol when it is available:

- Walk the decision tree one branch at a time.
- Ask exactly one question per turn and wait for the answer.
- Lead with a recommended answer and its short rationale.
- Inspect the repository for facts instead of asking the user.
- Leave product decisions to the user.
- Do not implement the feature while analyzing it.

When resuming a session or switching topics, briefly state the relevant agreed decision and the question now being resolved; do not repeat the full progress summary every turn. Make the recommendation's key trade-off concrete. For example, replace "How should offline mode work?" with "I recommend offline viewing first, with editing requiring a connection, so v1 avoids synchronization conflicts. Must users edit data while offline?" Adapt the recommendation to the actual requirements.

Cover these areas in the order dependencies require, not as a mechanical questionnaire:

- the user-visible problem and affected actors;
- desired outcomes and observable success;
- current experience and why it is insufficient;
- in-scope and out-of-scope behaviour;
- main flows, alternate flows, edge cases, and failure behaviour;
- compatibility, migration, security, performance, accessibility, or operational constraints when relevant;
- dependencies, assumptions, and unresolved risks.

Prioritize questions that change goals, scope, acceptance criteria, or decisions that would be expensive to reverse. For easily changed implementation details, record a reasonable default or defer to implementation instead of asking for approval item by item; do not use defaults to bypass material product decisions or explicit constraints.

Describe success as an observable benefit or behavior, not merely a list of components to build. Use quantitative targets only when supported by agreed requirements or evidence; never invent thresholds.

Skip irrelevant areas. Reopen an earlier answer when a later decision contradicts it.

## 4. Maintain the discussion document

After each material decision or coherent group of answers:

1. Update the canonical sections to reflect the current understanding.
2. Move resolved questions into `Decisions`, preserving the conclusion and rationale.
3. Append a dated session-log entry summarizing what changed and why.
4. Preserve unresolved uncertainty explicitly; never invent an answer to make the document look complete.

Before a handoff or a likely context boundary, persist the latest state even if analysis is incomplete. Redact secrets and unnecessary personal information.

## 5. Gate the PRD handoff

When the material requirements branches have been resolved, summarize:

- the problem and intended outcome;
- the agreed scope;
- the major behavioural decisions;
- any remaining open questions or risks.

Ask the user whether the analysis is ready to become a PRD. Do not declare it ready without explicit confirmation.

PRD readiness does not require every technical choice to be settled. Carry remaining design questions and their next steps into the handoff, distinguishing them from unresolved product requirements.

On confirmation:

1. Set `Status` to `ready-for-prd`.
2. Add the confirmation to the session log.
3. Tell the user to invoke `$to-spec` in the current conversation. If they will do so in another session, tell them to pass the `discussion.md` path.

`to-spec` remains responsible for producing and publishing the formal PRD. `to-tickets` remains responsible for breaking that PRD into implementation tickets.
