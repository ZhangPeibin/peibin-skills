---
name: to-feature-doc
description: Produce a detailed engineering document for an existing feature module by reading the actual source code, so a new engineer can understand its purpose, mental model, architecture, data flow, invariants, behaviour, and implementation.
disable-model-invocation: true
argument-hint: "<feature/module name or path>"
---

# To-Feature-Doc

Produce a single Markdown document that orients a **new engineer** around one existing feature or module — what it's for, how it's put together, how data flows, and where to look. The document describes what the code **is today**, grounded in the source: you read the code first, and every claim points at a real file a newcomer can open.

**The defining constraint:** you never document from memory of similar systems. If it isn't in the code (or git history, comments, ADRs), it doesn't go in the doc — and anything you infer is marked as inferred.

**Use diagrams when they improve understanding** — include architecture `flowchart`, API/service `sequenceDiagram`, state transitions, or other diagrams when they clarify relationships, ownership, or ordering. Every diagram must be accompanied by prose explaining what the reader should learn from it; a diagram without context is incomplete.

## Before you start

Pin down three things (ask the user if unclear):

- **Scope** — which feature, and its entry points / directories. The boundary decides what's in scope; state it explicitly.
- **Output path** — where the doc is written. Default to `docs/<feature>.md` in the target repo, or beside the module; follow the repo's existing docs convention if one exists.
- **Audience** — a new engineer with general skills but no context on this feature. Write for someone who will navigate the code, not just read prose.

## Workflow

Copy this checklist and track progress:

```
- [ ] 1. Scope: pin down entry points, boundaries, and what is out of scope
- [ ] 2. Read — purpose & domain: why this exists, key terms, invariants
- [ ] 3. Read — structure: components, layers, dependencies (not just call order)
- [ ] 4. Read — behaviour: main flows, state/lifecycle, error paths, side effects
- [ ] 5. Read — surface: external API, data models, config, feature flags
- [ ] 6. Read — operations: tests, scripts, deploy hooks, observability (if present)
- [ ] 7. Read — history: git history, comments, ADRs when they explain non-obvious decisions.
- [ ] 7.5 Invariant inventory: list invariants and where each will be expanded in the doc
- [ ] 8. Write the document using the template, grounded in the findings above (prose + diagrams)
- [ ] 9. Verify: grounding + completeness + invariants checklist
```

Complete the reading steps (2–7) before writing step 8. Do not interleave exploration and documentation — writing from a partial understanding tends to fill missing context with assumptions that are not grounded in the code.

### Reading dimensions

When tracing code, deliberately inspect multiple dimensions rather than only following the happy-path call chain. The goal is to understand the feature as a system: its purpose, boundaries, state, behaviour, and constraints.

| Dimension | What to extract | Where to look |
| --------- | --------------- | ------------- |
| Purpose | Problem solved, business/system responsibility, outcome|  README, package doc, top-level handler comments |
| Domain | Ubiquitous terms, entities, invariants | Types, DB schema, domain package |
| Structure | Layers, ownership boundaries, who calls whom | Package layout, imports, DI wiring |
| State | What is stored, when it changes, idempotency | Models, migrations, state machines |
| Behaviour | Triggers, branches, retries, async work, side effects | Handlers, workers, schedulers |
| Errors | Failure modes, validation, fallbacks | Error types, middleware, tests |
| Config | Env vars, flags, defaults, per-tenant overrides | config files, `.env.example`, feature flags |
| Extension | Where to add a new case without breaking others | Registries, interfaces, plugin points |
| Ops | How to run/test/observe locally | `Makefile`, tests, metrics, logs |
| **Invariants & continuity** | What must remain true across operations; state transitions that preserve consistency; formulas or conservation rules when applicable; what locks/idempotency protect | Domain rules, state transitions, pricing/settlement logic, balance updates, concurrency controls|

Skipping a dimension is allowed only when you confirm it does not apply. State this explicitly in the document.

**Module-type trigger:** If the feature involves **shares, unit price, settlement, ledger, balances, or accounting state**, complete an **invariants & continuity pass**. Extract formulas, numerator/denominator meaning, state transition constraints, and ordering requirements from code — not just the call sequence.

### Invariant inventory (step 7.5)

Before writing, build an internal table. Every row must appear **at least twice** in the doc: once in **Mental model → Invariants**, once expanded in the relevant **Core behaviour** subsection (not only in Gotchas).

| Invariant | Type | Brief meaning | Expand in doc section |
| --------- | ---- | ------------- | --------------------- |
| e.g. `(user_id, req_id)` idempotent | Concurrency | retries don't double-debit | Mental model → Subscribe / Redeem |
| e.g. unit-price continuity | Accounting | denominator includes redeeming; redeeming appreciation → fee | Mental model → Settlement → Consistency Statement |

Types: **Concurrency**, **Accounting**, **Temporal/ordering**, **Idempotency**. Omit types that don't apply; say so explicitly.

## Grounding rules

- **Read before you write.** Inspect the actual source files and trace the actual flow. Never fill gaps with how a system like this "usually" works.
- **Reference real paths**, relative to the repo root, so a newcomer can navigate the code. Prefer `path/to/file.ts:functionName` when a specific entry point matters.
- **Mark uncertainty.** Anything inferred but not confirmed by code, tests, comments, or design documents must be marked `(inferred)`. Do not invent design rationale.
- **Describe, don't editorialize.** Document what exists today. Known problems and sharp edges go in one **Gotchas** section, not sprinkled through as opinions.
- **Prose and diagrams together.** Major flows should have both narrative explanation and a diagram when ordering matters (especially API / service call chains). Either alone is weaker than both.

## Quality checklist

Reject and rewrite if the draft looks like any of these:

- **Diagram-only** — Mermaid blocks with almost no prose explaining components, data, constraints, or design rationale.
- **File list only** — a table of paths with one-line labels and no "how they fit together".
- **Happy path only** — describes success flow but omits errors, edge cases, or async side effects that exist in code.
- **Scope blur** — documents neighbouring modules in detail instead of naming them as dependencies.
- **Procedural black box** — lists step order without explaining the state transitions, constraints, or consistency rules that make the order correct.
- **Orphan invariant** — an invariant appears only in Gotchas or one bullet, not in Mental model **and** the behaviour section that enforces it.


## Document template

When writing the doc in step 8, follow the structure and section-by-section requirements in [TEMPLATE.md](TEMPLATE.md). Fill it from top to bottom; drop headings only when the feature genuinely does not have that concept rather than padding sections with generic content.


## Diagrams

Follow the **to-mermaid** skill for every Mermaid block. Do not invent your own quoting or shape rules — especially for file paths, API routes, database tables, and other identifiers.

**When to use which:**

- `flowchart` — component/layer relationships, dependency direction.
- `stateDiagram` — state machines and lifecycle transitions.
- `sequenceDiagram` — API call chains, service-to-service ordering, settlement/async pipelines. Include these for main flows when ordering matters, especially API/RPC/service calls, async pipelines, or external interactions.
- Keep each diagram **focused** — one flow or one structural view per block. Split a huge diagram into several smaller ones rather than one unreadable wall.

Use as many diagrams as needed to explain important concepts clearly. Prefer several focused diagrams over one overloaded diagram, but avoid diagrams that do not add understanding.

## Verify

Before finishing, confirm **both** grounding and completeness.

### Grounding

- Every referenced path exists.
- Every entry point is real.
- Described flows match what the code actually does.
- Inferred claims are explicitly marked as `(inferred)`.

### Completeness

The doc should let a newcomer answer **yes** to every applicable item:

- [ ] I know **why** this feature exists and what it does **not** own.
- [ ] I have a **mental model** before opening files.
- [ ] I understand the **domain terms** used in code and logs.
- [ ] I know the **main components** and how they depend on each other.
- [ ] I can follow the **primary behaviour** including failures and side effects.
- [ ] I understand the **invariants** that must remain true and where they are enforced.
- [ ] I know **how to invoke** it and what **data/config** it expects.
- [ ] I know **where to start reading** code and **how to test** locally.
- [ ] I know the **non-obvious traps** and where to read more.


### Invariants & continuity (when the module has stateful business rules, pricing, settlement, ledger, or balance consistency)

- [ ] **Mental model** has an **Invariants** table covering applicable invariant types (for example concurrency, accounting, lifecycle, or ordering).
- [ ] Every invariant in that table is **expanded** in a Core behaviour subsection, with a valid **Link** section reference.
- [ ] Settlement/pricing flows include **Consistency Statement** when applicable: formulas, numerator/denominator, and what shares/balances are included or excluded.
- [ ] Step order is explained as preserving an invariant, not only as a call sequence.
- [ ] No invariant lives **only** in Gotchas.

A document a newcomer cannot trust is worse than no document. A diagram without explanatory narrative is incomplete — add the context and re-verify.

A doc a newcomer can't trust is worse than none. A doc with diagrams but no prose context is not done — add the narrative and re-verify.
