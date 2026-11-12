---
name: blueprint
description: Draw a detailed architecture blueprint diagram for a feature across its app, backend, data, AI, and async boundaries.
disable-model-invocation: true
argument-hint: "<feature or spec>"
---

# Blueprint

A **blueprint** is one detailed architecture diagram. A feature is a vertical
slice, not a screen and not an endpoint: UI interaction, API contract,
service/domain rules, optional AI, persistence, async work, and failure paths.
An AI agent is one possible component, never the default.

**Scope:** this skill produces the diagram and a short caption only. It does not
write, refactor, or migrate code, and it does not run implementation or
verification commands. Implementation is a separate step, performed by another
skill or by the user.

## Before you start

Pin down the target:

- **Feature** — the user-visible outcome, spec, ticket, or module.
- **Repository** — read its instructions, commands, architecture rules, docs
  conventions, and worktree policy. Read-only; do not change files.
- **Stack** — identify the app, backend, database, queue/worker, model provider,
  and deployment constraints that already exist.

If several plausible features would materially change the diagram, ask one focused
question. Otherwise proceed with the best-supported scope and state the
assumption.

## Workflow

Gather the facts needed to draw, then draw once:

```
- [ ] 1. Feature contract and boundaries
- [ ] 2. Runtime path
- [ ] 3. Component decisions
- [ ] 4. Contracts and ownership
- [ ] 5. Backend and data inputs
- [ ] 6. App inputs
- [ ] 7. Async and context inputs
- [ ] 8. Failure, tests, and labels
- [ ] 9. Draw the blueprint
```

### 1. Feature contract and boundaries

Capture the outcome before components:

- who triggers the feature and from where;
- the user-visible result;
- success, empty, and failure behavior;
- inputs, outputs, and durable side effects;
- non-goals and adjacent features that must not be absorbed;
- any legal, privacy, ownership, or lifecycle constraints.

**Done when:** the feature can be explained in three sentences, and no unresolved
product decision changes the topology.

### 2. Runtime path

Trace the feature as one ordered path before choosing names or files:

```text
UI interaction
  -> app route / state / provider
  -> API contract
  -> backend service
  -> domain rules
  -> optional AI agent
  -> persistence / media
  -> optional async job / worker
  -> response
  -> UI state
```

Decide the layers and their owners: app, API, service, agent,
adapter/provider, queue, worker, database, or media.

**Done when:** every step has an input, output, and owner; no component is
implied but missing.

### 3. Component decisions

Decide each boundary with the tables below.

| Need | Use | Do not use |
| --- | --- | --- |
| Deterministic validation, calculations, ownership, state transitions | Service or domain code | AI agent |
| Language understanding, generation, extraction, semantic routing | Bounded AI agent with a structured contract | Rules hidden in the UI |
| Immediate user-visible result | Synchronous request | Fire-and-forget work |
| Durable retries, expensive side effects, delayed extraction | Queue/job + worker | Unbounded work in the request |
| Cohesive extension of an existing owner | Existing module/service | A parallel module with duplicated rules |
| New lifecycle, data ownership, or independent contract | New module + migration | Stuffing it into an unrelated module |
| Ephemeral client state only | App domain/provider/presentation | Backend storage |
| Shared or multi-client behavior | Backend contract + persistence | Device-local state |

For each component that will appear in the diagram, record: responsibility,
inputs, outputs, failure mode, and whether it owns persistence.

**Done when:** every component is labeled as deterministic, AI-backed, adapter,
worker, or client; no box has two owners.

### 4. Contracts and ownership

Collect the contract facts that become node and arrow labels:

- domain entities and invariants;
- API request/response shapes and stable error codes;
- AI agent input and structured output;
- DTOs and mappings at each boundary;
- ownership/account isolation;
- idempotency keys and deduplication where retries are possible;
- persistence records and lifecycle.

For AI agents, define the contract so the agent returns **candidates or
structured data**, while the service validates identity, permissions, limits, and
real database IDs. The agent never writes the database and never invents IDs.

**Done when:** happy path and failure path can both be drawn, and every durable
field has one writing owner.

### 5. Backend and data inputs

Note what the diagram must show on the backend side:

1. domain types and invariants;
2. service orchestration;
3. optional agent adapter behind a protocol, with a fake for tests;
4. database model and migration boundary when state is new;
5. router, authentication, rate limits, and error mapping.

For AI: one adapter, configuration loaded once, no real model key needed in tests.

**Done when:** the diagram can name the endpoint, its errors, the data store, and
the optional agent boundary.

### 6. App inputs

Note what the diagram must show on the app side:

- domain types and rules;
- DTO parsing and transport error boundary;
- API client and repository;
- providers for dependency wiring and state;
- presentation widgets and interaction logic;
- route registration and translations.

Keep infrastructure access out of presentation code. Keep semantic classification
and durable business rules out of the client; the client executes contracts and
renders states.

**Done when:** the diagram shows the real call chain from interaction to API and
back to UI state.

### 7. Async and context inputs

Include asynchronous work only when the request does not need the result:

- durable job/outbox record;
- debounce/idempotency policy;
- worker loop and retry budget;
- failure isolation from the main user action;
- evidence/source validation before persistence.

For AI context, decide which sources enter each call: profile, memory, error
history, recent conversation, prior experiences. Include relevance limits and
ownership boundaries. Do not infer private facts or turn practice into a real life
event.

**Done when:** asynchronous work is visibly separate from the synchronous
response, retries are bounded, and every injected context source has a stated
reason and lifecycle.

### 8. Failure, tests, and labels

Decide how each arrow and node is labeled, and what the caption must warn about:

- happy path vs. failure behavior on the same flow;
- error codes at the API boundary;
- idempotency and retry semantics;
- test seams that correspond to real boundaries (domain, service + fake agent,
  API contract, app DTO/provider/widget);
- optional real-provider smoke test and repository checks.

**Done when:** every arrow has a real call or data label and every failure the
reader must know is visible or in the caption.

### 9. Draw the blueprint

Draw one Mermaid diagram using `references/blueprint-template.md` and
`references/diagram-style.md`. Use `to-mermaid` for fragile labels.

- Layer with subgraphs: App, API, Service + Domain, optional AI, Data, and
  optional Async.
- Label every arrow with the real call, data, or error that crosses it.
- Use cylinders for databases and rounded rectangles for components.
- Omit optional components that do not exist in this feature.
- Add a second sequence diagram only when order, retries, or async handoff cannot
  be read from the flowchart.

Present the diagram and a 3–5 line caption, then stop. Do not enter
implementation. Ask one focused question only when a missing decision would
materially change the topology.

**Done when:** the reader can explain the feature from the diagram alone, and no
code or configuration was changed.

## Blueprint output

The whole deliverable is:

1. **Title** — the feature name.
2. **Caption** — at most five lines: trigger, user-visible outcome, key contract,
   failure/idempotency behavior, non-goals.
3. **Diagram** — one detailed flowchart in the style of
   `references/diagram-style.md`, or flowchart plus sequence when order matters.

Do not expand this into a multi-section document. A short ownership note or one
decision line is acceptable when the diagram cannot carry it.

Produce the diagram as the only working artifact. Stop after presenting it. Write
it to the repository's feature-doc location only when the user asks for a durable
document or the repository requires one.

## Guardrails

- Draw the actual shape, not the maximal architecture. Omit optional boxes that
  this feature does not use.
- Every arrow must represent a real call, event, or data flow. No decorative
  arrows.
- Put contracts, error codes, and async boundaries on the diagram rather than in
  prose. The caption is a caption, not a document.
- Start with the simplest architecture that satisfies the feature; add an agent,
  queue, cache, or abstraction only for a named force.
- Keep one agent per coherent contract. A giant agent with unrelated outputs is
  harder to test and to change.
- Keep deterministic ownership and persistence in services. AI proposes; the
  service validates and persists.
- Keep semantic routing in an AI boundary, not in client-side keyword or regex
  parsing.
- Bound context. Retrieve only what the current agent needs, with a clear source
  and lifetime.
- Isolate async failure. Background extraction must not fail the user's primary
  action.
- Reuse the repository's existing stack and conventions. A new dependency needs
  a concrete owner and boundary.
- Do not write, refactor, or migrate code while producing a blueprint. Do not run
  builds, tests, migrations, or formatters. Implementation is out of scope.

## Case studies

Read `references/named-case-studies.md` when the feature shape is unclear:
synchronous AI, async extraction, deterministic business logic, context assembly,
and intent routing. See `examples/bookmark-feature.md` for a complete rendered
blueprint.
