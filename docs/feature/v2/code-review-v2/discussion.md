# Code Review v2 - Requirements Discussion

**Status:** ready-for-prd
**Release:** v2
**Feature:** code-review-v2

## Problem

The current `code-review` skill is a strong evidence-gated review prompt, but its
centralized orchestration does not scale cleanly to large changes or continued
extension. The primary agent remains responsible for reading the whole change,
selecting concern-based lanes, and revalidating every result. Adding review domains
also grows the central instructions rather than extending a stable review topology.

## Users and Outcomes

- Engineers receive high-confidence reviews of local changes, commits, branches,
  ranges, and pull requests.
- Large changes can be reviewed by feature without losing cross-feature behavior.
- Skill maintainers can extend review behavior without continually enlarging the
  central prompt.
- Review coverage, reviewer failures, and unreviewed areas remain explicit.

## Current Experience

The v1 skill resolves a change scope, reads the complete diff, optionally dispatches
a small number of concern-specific lanes for high-risk changes, verifies candidate
findings, and returns a findings-first report. Delegation is optional and has no
deterministic feature partition, reviewer-pair topology, or scaling contract.

## Scope

### In Scope

- Feature-first decomposition of a resolved code change.
- A main agent that discovers the functional structure and creates Review Units.
- Two isolated adversarial reviewers for every Review Unit.
- A representation for shared and cross-feature behavior so interface defects are
  not lost between feature reviewers.
- Main-agent verification, deduplication, severity calibration, and final reporting.
- Explicit concurrency, failure, coverage, and extension contracts.
- Compatibility with the v1 review scopes and evidence-quality guarantees.

### Out of Scope

- Writing the formal PRD or implementation tickets during this analysis.
- Implementing v2 before the review topology and operational contracts are agreed.
- Sending repository code to external model providers by default.

## Required Behaviour

1. The main agent resolves the review scope and reads enough code and change context
   to identify functional responsibilities and their relationships.
2. The main agent produces a Feature Graph and partitions it into Review Units based
   on behavior and responsibility rather than directory boundaries alone.
3. Every Review Unit receives exactly two isolated adversarial review passes.
4. Reviewer A attacks the unit's internal behavior: business logic, state
   transitions, boundary conditions, and error paths.
5. Reviewer B attacks the unit's external contracts: callers, authorization, data,
   concurrency, compatibility, and deployment behavior.
6. Reviewers do not see each other's conclusions before returning their candidates.
7. Shared code and cross-feature contracts must have an explicit review owner rather
   than falling between feature units.
8. The main agent validates candidate findings against relevant evidence, removes
   duplicates, calibrates severity and confidence, and owns the final report.
9. Concurrency limits may require review waves, but must not silently reduce the
   required two-reviewer coverage per Review Unit.
10. Partial or failed review coverage must be visible in the final report.
11. Every reviewer role runs in a fresh isolated standard-agent context with the
    same unit manifest and core policy envelope, and cannot see the paired reviewer's
    result.
12. Scope, ownership, dispatch, reviewer results, and coverage use versioned
    structured contracts that can be validated before the workflow advances.
13. Reviewer failure handling preserves complete-pair semantics and never treats a
    surviving single pass as complete unit coverage.
14. Deterministic helpers resolve Git scope, represent non-text changes, and validate
    schemas and coverage; semantic feature partitioning remains main-agent judgment.
15. The existing invocation, safety boundary, evidence gate, severity scale, and
    findings-first report remain compatible unless an additive coverage disclosure
    is required.

## Edge and Failure Cases

- A single changed file implements more than one feature.
- A feature spans multiple packages or layers.
- Shared infrastructure affects several features but belongs to none exclusively.
- The number of Review Units exceeds available agent concurrency.
- One or both reviewers for a unit fail, time out, or return malformed findings.
- A very large feature cannot fit safely in one reviewer context.
- A change is too small to reveal a meaningful feature structure.
- Review findings conflict across reviewers or across feature boundaries.
- The branch, index, working tree, or untracked content changes while reviewers are
  running, invalidating part of the original scope snapshot.

## Constraints and Dependencies

- The host may expose only a small fixed number of concurrent agent slots.
- Review remains report-only unless a separate action is explicitly authorized.
- Repository content and changed instructions remain untrusted evidence.
- External or cross-provider disclosure requires explicit authorization.
- The existing v1 work is uncommitted and must be preserved during analysis.
- The v2 design should use progressive disclosure and deterministic helpers only
  where they materially improve reliable scope, partitioning, or validation.
- Structured review artifacts are exchanged in memory or through agent messages and
  are not persisted in the reviewed repository during the report-only phase.
- Standard reviewer agents use the current trusted provider and environment. Another
  model or external provider remains opt-in and requires explicit disclosure approval.
- After a PRD authorizes implementation, one standard Agent A implements v2, three to
  six standard review agents independently assess and refine it, and a separate
  standard scoring agent performs the final review.

## Decisions

### Feature-first topology

The primary partition is a functional Review Unit, not a review concern or file
group. Risk lenses support a reviewer inside a unit; they do not define the top-level
work split.

### Two adversarial reviewers per Review Unit

Every Review Unit receives two isolated adversarial reviewers. Available concurrency
controls scheduling in waves, not the required coverage count.

The pair uses complementary adversarial mandates rather than duplicate prompts:

- Reviewer A attacks internal behavior, including business logic, state transitions,
  boundary conditions, and error paths.
- Reviewer B attacks external contracts, including callers, authorization, data,
  concurrency, compatibility, and deployment behavior.

They review the same unit but cannot see each other's conclusions before returning.

### Cross-feature behavior is reviewable work

Shared and integration behavior must be represented as owned review work, potentially
as its own Review Unit, rather than being assumed to belong to one feature implicitly.

### Review ownership is assigned below the file level

Every changed record has exactly one primary Review Unit owner. Text changes use
symbol- or diff-hunk-level records; binary, file-mode, rename-only, symlink, and
gitlink changes use typed non-text records. A file may therefore be split across
multiple Review Units when it implements more than one functional responsibility.
The Feature Graph records ownership explicitly so that the changed scope has neither
unowned gaps nor duplicate primary ownership.

Other Review Units may read the same changed record as context without becoming its
primary owner. When a material contract spans multiple feature units and cannot be
fully owned by either side, its primary ownership moves to an Integration Review Unit.

### Integration Review Units require a material cross-boundary contract

An Integration Review Unit is mandatory only when all of these conditions hold:

1. A behavior or contract crosses at least two Feature Review Units.
2. No participating Feature Review Unit can validate the end-to-end contract by
   itself.
3. A defect at the interaction boundary could materially affect behavior, security,
   data integrity, concurrency, compatibility, migration, or deployment.

Imports, shared type references, and calls to stable utility functions do not create
an Integration Review Unit by themselves. Typical qualifying boundaries include
public APIs, data schemas, authorization-context propagation, transaction ordering,
concurrent coordination, migrations, and deployment compatibility.

The main agent may create an Integration Review Unit while building the initial
Feature Graph or later when reviewer evidence reveals a hidden cross-feature
contract. A late-created unit still receives the required two isolated adversarial
review passes; discovery timing does not reduce coverage.

### Oversized features use hierarchical behavioral subdivision

The Feature Graph distinguishes a logical Feature node from executable Review
Units. A Feature remains as a parent node to preserve its end-to-end purpose, but
the parent is not dispatched for an additional review pass after it is subdivided.
The main agent synthesizes the child results at the parent level.

A Feature is subdivided when its evidence cannot fit within the reviewers' reliable
context budget or when it contains multiple independently verifiable workflows.
Subdivision follows behavioral responsibilities, state transitions, or contract
boundaries rather than arbitrary file counts, directory layout, or equal-sized diff
chunks. Concrete size and context thresholds belong to the separate budget policy.

Every child Review Unit has explicit changed-record ownership and receives its own
pair of isolated adversarial reviewers. Material interactions between child units
use the same Integration Review Unit trigger as interactions between top-level
feature units.

### Budget overflow requires an explicit coverage decision

Before dispatching reviewers, the main agent performs a preflight that reports the
number of executable Review Units, the required reviewer passes (`2 x Review Units`),
the available concurrency, and the resulting review waves. Budget pressure must not
silently distort behavior-based partitioning or reduce a Review Unit to one reviewer.

When the plan exceeds a configured soft budget, the main agent pauses before
dispatch and asks the user to narrow the review scope, authorize an expanded budget,
or explicitly approve a risk-prioritized partial review. Partial review is performed
only in complete reviewer pairs: selected units receive both adversarial passes, and
unselected changed records are listed as uncovered in the final report.

Concurrency limits change scheduling rather than coverage. If a platform hard limit
cannot be satisfied through additional waves, the review stops with the affected
units and coverage gap reported instead of claiming completion.

### The standard profile allows six executable Review Units

The standard soft budget is six executable Review Units, requiring at most twelve
isolated reviewer passes. Feature child units and Integration Review Units count
toward this limit; non-dispatched parent Feature nodes do not.

The main agent reruns budget preflight whenever the Feature Graph gains a unit. A
late-discovered Integration Review Unit therefore counts against the same limit and
may require an explicit budget decision before its reviewers are dispatched.

### Reviewer concurrency is adaptive and wave count is derived

The standard concurrency cap is four active reviewer subagents. Actual concurrency
is the minimum of that cap, the host slots available after reserving the main agent,
and the number of pending reviewer passes. The main agent retains its own slot for
orchestration, verification, and aggregation.

Wave count is derived from reviewer passes and actual concurrency rather than being
an independent limit. Lower host concurrency increases the number of waves without
reducing coverage. Each reviewer role uses a fresh context even when review passes
must run sequentially; an agent that has seen one role's conclusions is not reused
as the isolated counterpart for that unit.

### Portable resource budgets use capability-aware controls

The standard profile defines these portable controls:

- at most six executable Review Units;
- at most four concurrent reviewer subagents, subject to host capacity;
- one valid result for each of the two reviewer roles per unit;
- one retry per failed reviewer role; and
- a default minimum context reserve of 30 percent for reviewer exploration,
  reasoning, and structured output.

A unit whose supplied working set would consume more than 70 percent of the usable
reviewer context is subdivided before dispatch. When exact context capacity is not
available, the main agent uses a conservative estimate and marks that estimate in
the preflight.

Token, command, per-pass timeout, and total-deadline caps vary materially across
hosts, so v2 exposes them as optional budget-profile fields rather than pretending a
single hard-coded number is portable. User-supplied and host-enforced limits take
precedence and are included in preflight. In their absence, the unit, concurrency,
context-reserve, and retry controls bound the standard workflow.

Reviewers may use narrow read-only inspection commands under the core safety rules.
They return proposed validation checks instead of independently running repeated
repository builds or tests. The main agent trust-checks, deduplicates, and runs only
the focused validations needed to adjudicate candidate findings.

### The main agent coordinates and verifies rather than repeating every review

The main agent reads the complete resolved diff or change manifest once, plus enough
surrounding context to construct and validate the Feature Graph. Deep unit analysis
belongs to the paired reviewers. The main agent then verifies candidate findings and
cross-unit claims against their evidence bundles, following additional context only
where a candidate or coverage invariant requires it.

This preserves final accountability without making the main agent perform a second
full review of every unit. It also keeps aggregation cost proportional to candidates
and cross-unit contracts rather than to every reviewer command or note.

### Reviewer failures receive one isolated retry

Every reviewer response must validate against the Reviewer Result schema. A timeout,
tool failure, malformed response, or missing required coverage is a failed attempt.
The main agent retries that role once in a fresh agent context with the same scope and
without the paired reviewer's conclusions. Context overflow causes behavioral
subdivision and a new budget preflight instead of retrying an unchanged oversized
unit.

If the retry also fails, the Review Unit is incomplete. Other units may continue,
but the final report identifies the failed role and its owned changed records as a
coverage gap. A candidate from the surviving reviewer may still become a finding if
the main agent independently verifies it, but it does not make the unit fully
reviewed. If both roles fail, the skill reports no review conclusion for that unit.

Reviewer disagreement does not require consensus and does not trigger a third
reviewer by default. The main agent adjudicates each claim through the evidence gate.
An unresolved factual conflict is reported as a question or verification gap, not as
a finding; a finding discovered by only one reviewer is valid when independently
verified by the main agent.

### Core workflow boundaries use versioned structured artifacts

Agent and helper boundaries use schema-versioned JSON-compatible records:

- **Scope Manifest:** scope kind, resolved object identities or local-state
  fingerprints, base and head, diff records, changed paths, exclusions, trusted
  requirement sources, and available validation commands.
- **Feature Graph:** Feature nodes, executable Review Units, material dependency
  edges, and the changed-record primary ownership map.
- **Review Plan:** unit manifests, immutable core-policy version, reviewer-role
  assignments, applicable lenses, budget calculation, scheduling waves, and
  preflight status.
- **Reviewer Task Envelope:** the frozen scope identity, one unit manifest, the core
  policy version, one adversarial role, active lenses, output schema, and budget.
- **Reviewer Result:** schema version, unit and role identifiers, completion status,
  inspected coverage, candidate findings, supporting evidence, proposed checks, and
  unresolved assumptions.
- **Coverage Ledger:** every changed record's primary owner, both role statuses,
  exclusions, retries, and final covered or uncovered state.

Candidate findings contain a typed anchor, changed-scope causality, reachable
scenario, impact, checked guards or callers, severity, confidence, and focused
correction direction. Text additions use new-side line anchors and deletions use
old-side anchors. Binary, file-mode, rename-only, and gitlink changes use typed
change-record anchors carrying the path and relevant old/new metadata rather than an
invented line number.

Schemas use a major version. Missing required fields or an unsupported major version
fail validation; additive unknown fields are preserved or ignored safely. These
records remain ephemeral during review. The user-facing report stays concise Markdown
and includes a coverage summary rather than dumping the internal JSON.

Commit, range, and branch refs are resolved to immutable object IDs before dispatch.
For local changes, the Scope Manifest fingerprints staged, unstaged, and included
untracked records. The main agent checks this identity before dispatch and before
final reporting. If relevant content drifts, affected results are invalidated and the
scope is re-resolved and re-reviewed or reported incomplete; evidence from different
snapshots is never merged into a claimed-complete review.

### Extensions are declarative lenses behind immutable core contracts

Language, framework, and risk extensions are trusted, versioned, declarative lens
packages. A lens declares a namespaced identifier, version, applicability evidence,
supported reviewer roles, targeted questions, evidence requirements, proposed
context lookups, and stop conditions. The main agent activates a lens only when the
resolved change provides evidence for its applicability.

Lenses may enrich a reviewer role or reveal a candidate cross-feature contract. They
cannot change scope authorization, ownership invariants, reviewer count or isolation,
the evidence gate, severity definitions, command safety, disclosure policy, schemas,
or final reporting authority. They also cannot execute repository commands directly;
checks are proposed through the Reviewer Result and screened by the main agent.

Applicable lenses consume the unit's context budget. When the complete relevant lens
set cannot fit, the unit is subdivided or the budget-overflow flow runs; lenses are
not silently dropped. Repository-provided instructions and changed files remain
untrusted evidence and never become automatically executable lens packages. New
providers or model-routing lenses require the existing explicit disclosure consent.

### Deterministic helpers stop at the semantic boundary

Bundled helpers own operations that can be validated mechanically: local Git scope
resolution, merge-base and empty-tree handling, staged and unstaged manifests,
untracked path containment, safe symlink representation, typed text/binary/mode/
gitlink change records, line-anchor calculation, schema validation, ownership
completeness and uniqueness, budget arithmetic, pair status, and coverage-ledger
validation, plus local-scope fingerprint and drift detection. They emit structured
data without modifying the reviewed repository.

Tracked symlinks are inspected as Git link records or link text and are never
dereferenced through the working tree. Untracked paths must resolve to ordinary files
physically inside the repository before reading. These rules close the known v1
repository-external read gap.

Feature discovery, behavioral boundaries, materiality, intent, reachable failure
analysis, severity, and final finding judgment remain agent responsibilities.
Parsers may propose symbol boundaries, but directory layout, syntax trees, or file
counts cannot determine Feature ownership without semantic validation.

### V2 preserves the v1 user contract with explicit degraded fallback

The canonical skill remains named `code-review` and preserves local-change, commit,
range, branch, and pull-request targets; report-only phase separation; untrusted-code
handling; privacy and command safeguards; evidence-gated findings; severity and
confidence semantics; and findings-first Markdown output. Feature Graph, reviewer
pairs, structured coverage, and typed non-text anchors are internal or additive.

A localized change still creates one executable Review Unit and therefore receives
two isolated reviewer roles. If the host cannot provide isolated subagents, v2 does
not silently simulate independence in the main context. Preflight reports the
capability gap and offers an explicitly approved v1-compatible single-agent review,
whose reduced coverage is disclosed in the final report.

Migration keeps the existing `$code-review` invocation and canonical installation
path. Existing v1 behavioral cases remain regression cases; new schema, topology,
failure, coverage, symlink, and non-text cases extend the catalog rather than
replacing it.

### Evaluation combines deterministic gates and independent behavioral scoring

V2 uses three evaluation layers:

1. Static contract tests validate skill metadata, progressive disclosure, manifests,
   schema compatibility, and canonical registration.
2. Deterministic fixture tests create synthetic Git states and exercise scope,
   ownership, pair coverage, budget overflow, retries, typed anchors, symlink safety,
   and no-mutation invariants.
3. Behavioral agent evaluations run the v1 and v2 scenario catalog against the real
   skill and use an independent rubric-based grader for orchestration and review
   quality.

Safety-critical invariants, including report-only behavior, scope correctness,
external-disclosure controls, path containment, ownership completeness, reviewer
isolation, pair coverage, and honest gap reporting, require a 100 percent pass rate.
The overall independent score must be at least the v1 baseline of 94/100, with no
unresolved Critical or High review finding against the skill itself.

The scoring rubric allocates 25 points to correctness and evidence quality, 20 to
safety, 20 to scalability, 15 to extensibility, 10 to v1 compatibility, and 10 to
testability. After Agent A implements the approved design, three to six isolated
standard review agents provide optimization feedback, Agent A addresses verified
issues, and a separate standard agent performs the final scored review.

## Open Questions

- None.

## Evidence

- `skills/personal/code-review/SKILL.md` contains the current centralized workflow.
- `skills/personal/code-review/references/risk-review.md` contains the current
  concern-oriented risk guidance.
- `skills/personal/code-review/tests/eval_cases.json` contains the current reusable
  behavioral evaluation inputs.
- Independent review scored v1 highly for ordinary source review while identifying
  scalability, extension, tracked-symlink, and non-text-diff limitations.
- The current contract test validates the evaluation catalog's schema and coverage
  labels but explicitly does not execute behavioral scenarios.

## Session Log

### 2026-08-27

- Established release `v2` and feature slug `code-review-v2`.
- Reframed the target architecture from concern-first delegation to feature-first
  Review Units.
- Required two isolated adversarial reviewers for every Review Unit.
- Assigned complementary internal-behavior and external-contract mandates to each
  reviewer pair while preserving reviewer isolation.
- Identified shared and cross-feature behavior as explicitly owned review work.
- Assigned every changed text symbol or diff hunk exactly one primary Review Unit
  owner, while allowing other units to read it as context.
- Chose below-file ownership instead of file-level ownership, extended it to typed
  non-text change records, and assigned contracts that cannot be coherently owned by
  one feature to an Integration Review Unit.
- Required an Integration Review Unit only when a material contract crosses at least
  two Feature Review Units, no one unit can validate it end to end, and a boundary
  failure could cause material impact.
- Excluded incidental imports, shared type references, and stable utility calls from
  triggering integration work, while allowing hidden contracts to create a unit
  later without waiving its two-reviewer coverage.
- Chose hierarchical subdivision for oversized features: retain a non-dispatched
  Feature parent, split executable child units along behavioral boundaries, and give
  every child its own isolated reviewer pair.
- Rejected file-count, directory, and equal-diff-chunk subdivision as primary rules;
  concrete context thresholds remain part of the budget decision.
- Required a dispatch preflight and explicit user choice when a review plan exceeds
  its soft budget; the skill cannot silently merge behavioral units or reduce their
  two-reviewer coverage to fit.
- Allowed explicitly authorized partial review only in complete reviewer pairs, with
  every uncovered changed record disclosed in the final coverage report.
- Set the standard soft budget to six executable Review Units and twelve isolated
  reviewer passes, counting both Feature child units and Integration Review Units.
- Required budget preflight to run again when the Feature Graph expands so that a
  late-discovered unit cannot bypass the approved limit.
- Delegated the remaining technical defaults to the recommended design: adaptive
  concurrency up to four reviewer subagents, derived waves, capability-aware resource
  limits, and one fresh-context retry per failed role.
- Required versioned structured scope, graph, plan, reviewer-result, and coverage
  records while keeping them ephemeral and preserving concise Markdown output.
- Froze every review to resolved object IDs or local-state fingerprints and required
  drift detection so parallel results from different snapshots cannot be merged.
- Defined declarative trusted lenses as the extension boundary and prevented them
  from overriding core safety, ownership, evidence, isolation, or reporting rules.
- Assigned mechanical Git, path-safety, typed-change, schema, budget, and coverage
  checks to deterministic helpers while retaining semantic partition and judgment in
  agents.
- Preserved the v1 invocation and report contract, added only an explicit degraded
  fallback when isolated subagents are unavailable, and made all existing behavioral
  cases v2 regression inputs.
- Established deterministic and behavioral release gates, including 100 percent on
  safety-critical invariants and an independent final score of at least 94/100 after
  the requested Agent A and multi-agent review workflow.
- Received explicit user confirmation that the analysis is ready for PRD handoff and
  changed the discussion status to `ready-for-prd`.
