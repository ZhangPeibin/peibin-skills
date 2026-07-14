---
name: to-feature-doc
description: Produce a detailed function document for an existing feature module, grounded in the actual code, so a new engineer can understand its whole design.
disable-model-invocation: true
---

# To-Feature-Doc

Produce a single Markdown document that orients a **new engineer** around one existing feature or module — what it's for, how it's put together, how data flows, and where to look. The document describes what the code **is today**, grounded in the source: you read the code first, and every claim points at a real file a newcomer can open.

The defining constraint: you never document from memory of similar systems. If it isn't in the code (or git history, comments, ADRs), it doesn't go in the doc — and anything you infer is marked as inferred.

**Use diagrams freely where they help** — architecture `flowchart`, API/service `sequenceDiagram`, state transitions, etc. Pair each diagram with prose so a reader gets both the picture and the context; a diagram with zero explanation is not enough.

## Before you start

Pin down four things (ask the user if unclear):

- **Language** — default to **Chinese**; switch to the repo's docs language if there's an existing docs convention in another language.
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
- [ ] 5. Read — surface area: external API, data models, config, feature flags
- [ ] 6. Read — operations: tests, scripts, deploy hooks, observability (if present)
- [ ] 7. Read — history: git log, comments, ADRs for non-obvious decisions
- [ ] 7.5 Invariant inventory: list invariants and where each will be expanded in the doc
- [ ] 8. Write the doc from the template (prose + diagrams)
- [ ] 9. Verify: grounding + completeness + invariants checklist
```

Do the reading (steps 2–7) before writing (step 8). Don't interleave — a half-read feature produces a doc that quietly invents the other half.

### Reading dimensions

When tracing code, deliberately look for **more than the happy-path call chain**:

| Dimension | What to extract | Where to look |
| --------- | --------------- | ------------- |
| Purpose | Problem solved, user/system outcome | README, package doc, top-level handler comments |
| Domain | Ubiquitous terms, entities, invariants | Types, DB schema, domain package |
| Structure | Layers, ownership boundaries, who calls whom | Package layout, imports, DI wiring |
| State | What is stored, when it changes, idempotency | Models, migrations, state machines |
| Behaviour | Triggers, branches, retries, async work | Handlers, workers, schedulers |
| Errors | Failure modes, validation, fallbacks | Error types, middleware, tests |
| Config | Env vars, flags, defaults, per-tenant overrides | config files, `.env.example`, feature flags |
| Extension | Where to add a new case without breaking others | Registries, interfaces, plugin points |
| Ops | How to run/test/observe locally | `Makefile`, tests, metrics, logs |
| **Invariants & continuity** | What quantities are conserved; formula numerator/denominator; why step order preserves consistency; what locks/idempotency protect | Pricing/settlement functions, `div`/`mul` on shares/balances, pool/position updates, `Lock*`, settlement pipeline order |

Skipping a row because it "doesn't apply" is fine — **state that explicitly** in the doc (e.g. "无持久化状态").

**Module-type trigger:** if the feature involves **shares, unit price, settlement, ledger, or balances**, you must complete an **invariants & continuity pass** — extract formulas and denominator meaning from code, not just call order.

### Invariant inventory (step 7.5)

Before writing, build an internal table. Every row must appear **at least twice** in the doc: once in **Mental model → 不变量**, once expanded in the relevant **Core behaviour** subsection (not only in Gotchas).

| Invariant | Type | Brief meaning | Expand in doc section |
| --------- | ---- | ------------- | --------------------- |
| e.g. `(user_id, req_id)` idempotent | Concurrency | retries don't double-debit | Mental model → Subscribe / Redeem |
| e.g. unit-price continuity | Accounting | denominator includes redeeming; redeeming appreciation → fee | Mental model → Settlement → 一致性说明 |

Types: **Concurrency**, **Accounting**, **Temporal/ordering**, **Idempotency**. Omit types that don't apply; say so explicitly.

## Grounding rules

- **Read before you write.** Open the actual files and trace the actual flow. Never fill gaps with how a system like this "usually" works.
- **Cite real paths**, relative to the repo root, so a newcomer can click through. Prefer `path/to/file.ts:functionName` when a specific entry point matters.
- **Mark uncertainty.** Anything you deduce but can't confirm from the code gets `(inferred)`. Design rationale you can't derive — ask the user to confirm rather than guessing a reason.
- **Describe, don't editorialize.** Document what exists today. Known problems and sharp edges go in one **Gotchas** section, not sprinkled through as opinions.
- **Prose and diagrams together.** Major flows should have both narrative and a diagram when the flow has meaningful ordering (especially API / service call chains). Either alone is weaker than both.

## Anti-patterns (insufficient doc)

Reject and rewrite if the draft looks like any of these:

- **Diagram-only** — Mermaid blocks with almost no prose explaining components, data, constraints, or design rationale.
- **File list only** — a table of paths with one-line labels and no "how they fit together".
- **Happy path only** — describes success flow but omits errors, edge cases, or async side effects that exist in code.
- **Scope blur** — documents neighbouring modules in detail instead of naming them as dependencies.
- **Procedural black box** — lists step order (e.g. yield → redeeming → pending) without formula-level continuity (numerator, denominator, what's in the pool).
- **Orphan invariant** — an invariant appears only in Gotchas or one bullet, not in Mental model **and** the behaviour section that enforces it.

## Document template

When writing the doc in step 8, follow the structure and section-by-section instructions in [TEMPLATE.md](TEMPLATE.md). Fill it top to bottom; drop any heading the feature doesn't actually earn rather than padding it.

## Diagrams

Follow the **to-mermaid** skill for every Mermaid block. Do not invent your own quoting or shape rules — especially for file paths, API routes, and table names.

**When to use which:**

- `flowchart` — component/layer relationships, dependency direction, state transitions.
- `sequenceDiagram` — API call chains, service-to-service ordering, settlement/async pipelines. **Include these for main flows** (subscribe, redeem, settlement, etc.) when the module exposes HTTP/RPC entry points.
- Keep each diagram **focused** — one flow or one structural view per block. Split a huge diagram into several smaller ones rather than one unreadable wall.

No cap on diagram count. If a feature has five important flows, five sequence diagrams is fine.

## Verify

Before finishing, confirm **both** grounding and completeness.

### Grounding

- Every cited path exists.
- Every entry point is real.
- Described flows match what the code actually does.

### Completeness

The doc should let a newcomer answer **yes** to all that apply:

- [ ] I know **why** this feature exists and what it does **not** own.
- [ ] I have a **mental model** before opening files.
- [ ] I understand the **domain terms** used in code and logs.
- [ ] I know the **main components** and how they depend on each other.
- [ ] I can follow the **primary behaviour** including failures and side effects.
- [ ] I know **how to invoke** it and what **data/config** it expects.
- [ ] I know **where to start reading** code and **how to test** locally.
- [ ] I know the **non-obvious traps** and where to read more.

### Invariants & continuity (when the module has state, pricing, settlement, or ledger)

- [ ] **Mental model** has an **不变量** table covering concurrency and accounting (if applicable).
- [ ] Every invariant in that table is **expanded** in a Core behaviour subsection, with a **详见** link target that exists.
- [ ] Settlement/pricing flows include **一致性说明**: formulas, numerator/denominator, what shares/balances are included or excluded.
- [ ] Step order is explained as preserving an invariant, not only as a call sequence.
- [ ] No invariant lives **only** in Gotchas.

A doc a newcomer can't trust is worse than none. A doc with diagrams but no prose context is not done — add the narrative and re-verify.
