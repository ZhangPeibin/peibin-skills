# Continuation Payloads

Select one or more payloads according to the work represented in the session. Under `## Continuation Payload`, add one `### <Type>` heading per selected type. Use `### General` only when no specific type holds the unrecoverable context.

Do not repeat repository state, global task state, test/build/lint/validation commands or results, decisions, risks, blockers, or the global next action from the stable core, except where a rule below assigns action details to the payload and requires an exact Next Action cross-reference. Conversely, keep type-specific details in their payload rather than copying them into Goal, Decisions, Assumptions, or Constraints. A lifecycle summary in `Current State` must not duplicate the observations, experiment results, paths, counts, or session-local material in a payload. If another section needs a payload fact to remain intelligible, use a short cross-reference instead of restating it. Prefer precise type-specific context and verbatim transient work. Apply `[V]` and `[?]` to drift-prone payload claims as defined in `SKILL.md`, wherever they occur. Payload routing may preserve analysis explicitly present in the source, but it must not create missing analysis, hypotheses, causal explanations, risks, decisions, or actions.

## Coding

Include only applicable implementation-specific details:

- Important entry points, interfaces, schemas, migrations, generated-code boundaries, and compatibility constraints.
- Changed files only when their roles are necessary to understand the implementation and are not already clear from a durable diff.
- Processes or services still running, including how the successor can identify them. Put any safety or stop procedure under Operations instead.
- Uncommitted code or patches that exist only in the conversation, preserved inside the transient-data fence required by `SKILL.md`.

Do not paste recoverable source or a full Git diff.
Do not repeat that implementation or tests are complete, partial, or unfinished; those lifecycle claims belong only in `Current State`.

## Debugging

Include:

- The user-visible symptom and expected behavior.
- Minimal reproduction inputs and environment details specific to the failure.
- Confirmed observations separated from hypotheses.
- Non-test experiments already performed, their actual outcomes, and what those outcomes establish. For test-derived evidence, refer briefly to `Evidence And References` and keep only the diagnostic interpretation here.
- Failed approaches, ruled-out hypotheses, and known traps.
- Possible outcome interpretations already supplied for the next experiment, without restating the global action from `Next Action`.

Never promote a hypothesis to fact merely because it is the leading explanation.
When Goal already owns the expected behavior as a completion condition, do not restate it here; use `Expected behavior: see Goal And Definition Of Done` only when the payload would otherwise be ambiguous.
Keep detailed symptoms, experiments, hypotheses, and outcome interpretations here. Use stable Decisions only for a consequential past decision that remains meaningful independently of the next discriminator; do not copy a debugging hypothesis into stable Decisions or Assumptions.
Do not restate the command or investigation step from `Next Action`; include only what each possible result would establish.
Investigation ordering belongs only in `Next Action`, even when the user phrases that ordering as a boundary. Keep the detailed hypothesis and discriminator here; stable Remaining, Open Questions, and Risks must stay generic or `None` rather than naming them again. For a debugging handoff, `Current State` must never say that experiments or hypotheses were performed, narrowed, ruled out, or remain possible; it states only whether the investigation is open or complete.
Never put investigation ordering or a hypothesis boundary in Goal; Goal states only the product or diagnostic outcome and observable completion conditions.

## Writing Or Design

Include:

- Current structure and accepted direction when not already durable.
- Any draft, exact wording, prompt, diagram source, or design material that exists only in the conversation, preserved inside the transient-data fence required by `SKILL.md`.
- Intended audience, tone, format, language, length, and style constraints specific to the work product.
- Rejected directions and why they should not be reopened.
- Specific unresolved sections or review questions.

Reference durable drafts by path instead of duplicating them. When a verbatim transient fence already contains an audience, tone, format, or other requirement, do not repeat that requirement in prose around the fence.
Keep audience, tone, format, and other work-product-specific requirements here rather than repeating them as global Constraints.
Keep whether the draft is complete or unfinished only in `Current State`; this payload owns the actual draft and its product-specific requirements.
If the source says a draft is unfinished but supplies no content needed to finish it, preserve the draft here, put the missing product input in stable Open Questions, and make Next Action request that input from its known owner. Do not invent a generic completion action.
For a verbatim draft, include only a short source/type label before the fence, preserve exactly the draft body rather than surrounding conversational setup, and end the payload immediately after the closing fence. Do not add a summary or unresolved-details prose after it.

## Research

Include:

- The research question and decision it is intended to support.
- Source claims clearly separated from source-session conclusions.
- Conflicting evidence, methodological limitations, confidence limits, and evidence gaps.
- Branches not yet investigated and the source or query that would distinguish them.

When Goal already owns the research question or outcome, do not restate it here. Include only a distinct downstream decision, or start directly with the source claims when no separate decision was supplied.
Keep primary-source URLs in `Evidence And References`; do not repeat them here.
Keep the source claims, source-stated conclusion boundary, conflict, and methodological limits only here; the Goal states the research outcome sought, not those claims.
This payload also owns the exact evidence-gap specification and discriminating query. If that evidence must come from another person or system, stable Open Questions and Next Action use only the named-section cross-reference pattern from `SKILL.md`.

## Operations

Include:

- External side effects already performed and their identifiers.
- Access prerequisites without credentials or private identity data.
- Rollback, recovery, and safe-stop procedures, including trigger conditions.
- Long-running operations, ownership, observation method, and safe termination method.
- Environment-specific safety boundaries not already stated as global constraints.

Mark an operation active only when currently verified; otherwise state that its status must be rechecked. Never include secrets.
Keep operation identifiers, observation methods, owners, thresholds, and rollback steps out of Goal. Keep environment there only when it is part of a distinct source-supplied target; otherwise Goal is `None`.
`Current State` may say only `Operational follow-up remains.` in one subsection; it must not say an operation started, is active, was observed, or has a status. This payload owns the specific operation's identifiers, reported or verified status, ownership, observation, rollback, and safe-stop details; do not copy those details into Goal, stable Constraints, or the lifecycle summary.
When Operations owns any part of the next action, put its complete source wording, order, and conditions in one `Action:` item here. Do not repeat its observation method, trigger, stop, or rollback as other payload items. Use exactly `Source-prescribed next action: Follow the action in Continuation Payload > Operations.` in Next Action. Do not restate operation identifiers, console, status, threshold, owner, rollback, or safe-stop details there or in another stable-core field. After writing environment in Resume Context, delete it from Operation and Status items; keep it only inside exact Action wording such as a console name.

## General

General may coexist with specific payload types. Include only session-local material that the successor cannot recover from referenced artifacts and that does not fit any selected specific payload. State why it matters to continuation. Do not use this payload to copy the transcript, repeat stable-core facts, duplicate another payload, or preserve conversational trivia.

## Completed Work

When the stated goal is complete, still select the relevant payload type. Keep `In Progress` and `Remaining` as `None` unless a real obligation remains. Do not turn optional enhancements, unavailable source access, or hypothetical future authorization into unfinished work or a risk. If nothing remains, set Risks and Active Blockers to `None` unless the source session supplied a real residual consequence, and set `Next Action` to `None - the stated goal is complete.`
