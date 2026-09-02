# Risk-Directed Review

Use this reference only when the reviewed change touches a high-consequence boundary
or has a broad compatibility surface. Select the relevant checks; this is not a
mandatory checklist for every change.

## History and rationale

Use `git log`, path history, or `git blame` when the change removes a guard, alters
an established invariant, revisits a subtle implementation, or conflicts with an
older test or comment. Look for the commit that introduced the behavior and the
reason recorded there. History is context, not authority: verify that its assumptions
still apply.

Do not perform blame mechanically on every changed line. Stop once history explains
the invariant or provides no additional evidence.

## Blast radius

Trace outward from the changed boundary until its consumers and compatibility
obligations are understood:

- direct callers, implementations, overrides, and registration sites;
- serialized, stored, cached, or wire representations;
- downstream services, CLI or UI consumers, and public exports;
- configuration defaults, feature flags, deployment order, and rollback behavior;
- tests that encode contracts or exercise alternate implementations.

Search results indicate possible consumers, not proof of runtime reachability.
Inspect the relevant call path before reporting a finding.

## Targeted failure models

### Security and trust boundaries

Identify attacker-controlled inputs and the identity or tenant context at each
decision. Check authentication and authorization ordering, fail-open defaults,
confused-deputy paths, injection or traversal sinks, sensitive logs, and whether a
validation step is applied before every reachable use rather than only one caller.

### Persistent data and migrations

Consider old and new application versions during rollout and rollback, existing
rows, partial execution, transaction boundaries, constraint ordering, backfill
restartability, duplicate execution, and whether destructive conversion is
recoverable. Verify both schema and application assumptions.

### Concurrency, retries, and asynchronous work

Construct concrete interleavings. Check atomicity, lock scope and ordering,
idempotency, duplicate delivery, cancellation, timeout, retry amplification, and
partial success. A race finding must name the competing operations and the state
that becomes invalid.

### Public APIs and compatibility contracts

Trace all known consumers and representations. Check default changes, optionality,
error semantics, version negotiation, enum expansion, ordering guarantees, and
forward/backward compatibility. Distinguish source, binary, wire, and stored-data
compatibility rather than calling every interface change breaking.

## Apply risk questions inside the required pair

After this inspection establishes a relevant risk, add `risk:security`, `risk:data`,
`risk:concurrency`, or `risk:compatibility` to the affected Review Unit's semantic
applicability evidence. The installed declarative lenses route only the matching questions
through that unit's exact two roles. Read
[resilience-and-lenses.md](resilience-and-lenses.md) for the lens IDs and immutable
extension boundary.

Internal-behavior reviewers attack state, invariants, transitions, and error paths;
external-contract reviewers attack trust propagation, consumers, persistence,
concurrency, compatibility, migration, and deployment. Do not create concern-specific
lanes in addition to, or instead of, the required pair. Each role returns candidate
findings with typed anchors, reachable scenarios, checked guards or callers, severity,
confidence, and unresolved assumptions.

Keep all review work inside the authorized environment. Do not send code to a
third-party service or a different model provider without explicit user approval.
