# V2 Contracts: Feature Graph Orchestration

Use this reference after scope resolution when constructing, preflighting, or dispatching
the Feature Graph. All six schema-bearing contracts are JSON-compatible and require a
canonical `major.minor` string: `scope_manifest`, `feature_graph`, `review_plan`,
`reviewer_task_envelope`, `reviewer_result`, and `coverage_ledger`. Decimal components have
no signs or leading zeroes except the single digit `0`, and each component is limited to
10 digits before integer conversion. Shortened, padded, patch-level, or oversized forms
fail validation with a controlled contract error. The supported major is `2`; compatible
future minor profiles within that bound remain valid. Missing required fields and another
major version also fail validation. Additive fields are retained or ignored safely except
in Reviewer Task Envelopes, whose reviewer-visible fields are closed to preserve isolation.

Current `build-graph` output uses minor profile `2.1` for Review Plans and Reviewer Task
Envelopes. That profile requires resilience, lens, mandate, and canonical-budget fields;
removing them cannot turn a current bundle into legacy work through `complete-graph`.
The separately invoked `complete-legacy-graph` command is the explicit compatibility
boundary for all-success `2.0` plans and envelopes produced before those extensions.

The helper reads JSON from stdin and writes JSON to stdout:

```bash
python3 scripts/review_contract.py validate --kind scope_manifest
python3 scripts/review_contract.py build-one-unit --unit-id <stable-id> --feature-name '<name>'
python3 scripts/review_contract.py build-graph
python3 scripts/review_contract.py validate --kind reviewer_result
python3 scripts/review_contract.py complete-graph
python3 scripts/review_contract.py complete-legacy-graph
python3 scripts/review_contract.py complete-one-unit
python3 scripts/review_contract.py scope-gate
python3 scripts/review_contract.py advance-review
python3 scripts/review_contract.py screen-validations
python3 scripts/review_contract.py finalize-review
python3 scripts/review_contract.py validate --kind lens
```

For the executable regression harness and independently derived release grade, read
[quality-gates.md](quality-gates.md). These evaluator-only commands exercise the public
workflow without invoking providers or writing artifacts into reviewed repositories.

`build-one-unit` and `complete-one-unit` preserve the localized v2 baseline. For a semantic
graph, pass `scope_manifest`, `feature_graph`, and `environment` to `build-graph`. Optional
`budget_decision` records an explicit user choice. Optional `previous_review_plan` forces a
revisioned preflight after graph growth. Keep all inputs and outputs in process memory or
agent messages, never in the reviewed repository.

For bounded retries, snapshot gates, degraded fallback, validation-proposal screening,
candidate adjudication, and the declarative lens schema, read
[resilience-and-lenses.md](resilience-and-lenses.md).

## Contract ownership

- **Scope Manifest:** frozen target identity, immutable base/head or local fingerprint,
  typed changed records, optional unchanged context records frozen from an immutable
  revision, exclusions, trusted requirement sources, and proposed validation commands.
- **Feature Graph:** logical Features, executable Feature and Integration Review Units,
  material edges, parent-child relationships, and exactly one primary owner for every
  changed record. Context-only records do not alter ownership.
- **Review Plan:** frozen scope, graph, active-lens, and canonical-budget identities,
  core-policy version, selected units, paired assignments, resource limits, waves, and
  preflight status.
- **Reviewer Task Envelope:** task, scope, unit, role, owned records, immutable core policy,
  graph and lens revisions, output contract, and budget. The two envelopes have the same
  evidence and policy but distinct mandates. Never add peer conclusions or progress. Task
  IDs bind the scope, graph revision, active lenses, canonical per-role budgets, unit, and
  role so results from superseded evidence or resource decisions cannot be accepted as
  current work.
- **Reviewer Result:** task, scope, unit, role, status, inspected record IDs, candidate
  findings, supporting evidence, proposed checks, and unresolved assumptions.
- **Coverage Ledger:** every record's primary owner plus record- and unit-level role states,
  attempts, covered status, and an explicit reason for every uncovered entry. A record and
  its owning unit are covered only when both roles have valid completed results. Covered
  entries have no uncovered reason; `uncovered_record_ids` contains every uncovered record
  exactly once. Context-only Integration Review Units remain visible at unit level; the
  ledger is complete only when every record and executable unit is covered.

Current Coverage Ledgers always include unit rows. Every record owner names one of those
units, and each role's state and attempt count are one bounded invariant: pending,
degraded, and not-selected states have zero attempts; retry-pending has one; completed has
one or two; incomplete has two; and no current role exceeds two attempts. Graph completion
and lifecycle entry points additionally require the ledger's unit set to equal the frozen
executable graph exactly.

## Semantic graph input

Read the complete resolved change before creating the graph. The helper validates a graph;
it never discovers features or assigns intent. Do not construct features from directories,
file counts, syntax alone, or equal-sized chunks.

A Feature declares a stable `feature_id`, name, purpose, and its executable
`review_unit_ids`. A Feature Review Unit declares its Feature, purpose, owned
`changed_record_ids`, optional `context_record_ids`, and an
`estimated_context_tokens` value when capacity is known. The Scope Manifest emits a
separate record for each text diff hunk, so distinct responsibilities in one file can have
different primary owners. Non-text and untracked changes remain typed whole-record units.
Every Scope Manifest record must occur in one unit's owned records and map to that unit in
`ownership`; context may overlap ownership but never changes it.

Feature-to-unit links are reciprocal: each Feature Review Unit's `feature_id` must name
the Feature whose `review_unit_ids` contains it. A schema-major-2 localized unit created
by the prior baseline may omit the newer unit `purpose` field; accept that input while new
semantic graphs continue to supply a purpose.

Features with children are logical aggregation nodes. They declare reciprocal
parent-child links and a `subdivision` reason plus a `behavior`, `state_transition`, or
`contract` boundary. A logical parent has no Review Unit. Its executable children receive
the reviewer pairs and the main agent synthesizes their results.

## Material integration

Create an Integration Review Unit only when all three semantic conditions hold:

1. A named material contract crosses at least two Feature Review Units.
2. No participating Feature Review Unit can validate the contract end to end.
3. A boundary defect could materially affect behavior, security, data integrity,
   concurrency, compatibility, migration, or deployment.

Record those facts in `integration_criteria`, list every participating feature unit, and
add a `material_contract` edge from every participant to the Integration Review Unit.
Incidental imports, shared types, or stable utility calls do not satisfy the conditions.
Apply the same rule between subdivided children as between top-level features.

## Preflight and budget

The standard profile allows six executable Review Units, twelve required passes, and four
active reviewer agents after the main agent's slot is reserved. `available_agent_slots`
includes the main agent. Effective reviewer concurrency is therefore the smaller of four,
available slots minus one, and pending passes. Lower capacity increases the derived wave
count without reducing paired coverage.

Provide `usable_context_tokens` and a conservative per-unit estimate when available. No
unit may be supplied more than 70 percent of usable context; the remaining 30 percent is
reserved for exploration, reasoning, and output. Applicable lens cost is added separately
for each reviewer role; unit preflight uses the larger role estimate, not the sum of
role-exclusive lenses. Active lens content and the canonical per-role budgets are bound
into task identity. A context-overflow preflight pauses for behavioral subdivision and
emits no task envelopes; never drop an applicable lens to make a task fit. Optional user
and host resource limits are reported; where both provide a numeric ceiling, the stricter
host-compatible value is effective.

A standard-profile overflow pauses before dispatch and offers only:

- a smaller resolved scope and rebuilt graph;
- an explicitly approved `budget_expansion`, bound to the current `graph_revision`, with
  adequate unit and pass ceilings; or
- an explicitly approved `paired_partial_review`, bound to the current `graph_revision`,
  with risk-prioritized whole-unit IDs.

Partial review emits both roles for every selected unit. Its preflight and Coverage Ledger
list every unselected unit and owned record, mark those records not selected, and never
claim complete coverage. Lifecycle and completion operations revalidate the explicit,
graph-bound `paired_partial_review` approval before accepting work. A scope-reduction
decision applies only after producing the smaller Scope Manifest and graph; a label cannot
make an oversized graph ready.

Pass the last Review Plan as `previous_review_plan` whenever a discovery changes the graph.
The next preflight increments its sequence, compares graph fingerprints, records added
units and the trigger, recalculates ownership and all limits, and emits no envelopes if the
new graph exceeds budget. A late Integration Review Unit never inherits an earlier budget
approval silently.

## Graph completion

Pass the frozen bundle and every selected Reviewer Result to `complete-graph`. The helper
accepts only a ready or ready-partial plan whose graph fingerprint is still current. It
recomputes revision-bound task IDs and verifies every assignment, envelope, owned and
context record set, result role, completed status, inspected ownership, candidate record
identity and anchor, and pending ledger entry before advancing coverage.

The explicitly invoked `complete-legacy-graph` path accepts an earlier `2.0` plan that
predates lens, role-mandate, and canonical-budget fields and returns
`review_mode: legacy_v2_all_success`. Ordinary `complete-graph` rejects `2.0` and current
`2.1` completion returns `review_mode: resilient_lenses_v2`. New lifecycle operations
require the current extensions because retry isolation and budget provenance depend on
them.

A full plan completes only after both roles finish every executable unit. A paired partial
review advances the selected units and their owned records while preserving every
unselected record and unit as uncovered, so `coverage_ledger.complete` remains false.
Before review, selected entries use `review_pending`; a paused preflight replaces the
reason with `preflight_blocked:<reasons>`. Completion clears the reason only for entries
whose two roles completed and recalculates `uncovered_record_ids` from actual coverage.
Context-only Integration Review Units have no owned record rows, but their unit-level pair
state still prevents a complete claim until both current-revision results are accepted.
`complete-one-unit` remains the compatible localized entry point and applies the same
checks after requiring exactly one Review Unit.

## Reviewer result

A successful reviewer returns `status: "completed"` and all owned record IDs in
`inspected_record_ids`. It may inspect additional context, but must not claim that context
as owned coverage. Every candidate finding contains:

- `finding_id` and concise `title`;
- the owned `changed_record_id` plus that exact Scope Manifest record's line or typed
  change `anchor`;
- `changed_scope_causality`;
- `reachable_scenario` and `impact`;
- `checked_context`, including relevant guards, callers, tests, or types;
- `severity` and `confidence`; and
- `correction_direction`.

These fields are required and their known values remain validated. A candidate may also
carry additive fields from a compatible later minor profile; validation retains those
fields so downstream consumers can use or ignore them safely. This forward-compatible
rule does not apply to Reviewer Task Envelopes, whose closed reviewer-visible field set
continues to reject unknown context.

Only `high` and `medium` confidence candidates are contract-valid. A controlled result
that attempts to return low-confidence speculation is rejected before candidate content is
accepted; the lifecycle may issue its single fresh isolated retry, and the speculation must
not appear in final findings or Markdown.

The explicitly approved single-agent fallback uses this same candidate and evidence
contract. It does not gain a weaker schema or finding path merely because its independence
and Coverage Ledger remain degraded.

Return an empty candidate array when nothing survives the reviewer's own scrutiny. Put
uncertain product decisions in `unresolved_assumptions` as closed
`{assumption_id, text}` records and safe, focused structured argv records in
`proposed_checks`; reviewers do not execute those checks independently. Assumption IDs
must be unique within a result. Malformed assumptions consume the same bounded lifecycle
retry as other malformed reviewer output and are never accepted for rendering.
Finalization retains accepted unresolved assumptions as questions with task, unit, and
reviewer-role provenance.

Validate each result before accepting it. `complete-graph` additionally confirms that task,
role, unit, scope ID, graph revision, and inspected ownership match the frozen plan and that
both roles completed for every selected unit. Each candidate's `changed_record_id` must be
owned by the unit, and its anchor must exactly match that same frozen Scope Manifest record;
an equal anchor from a different staged, unstaged, or commit record is not interchangeable.
It returns candidates for main-agent adjudication, not final findings.

`finalize-review` accepts only closed structured main-agent evidence. A code item names one
exact `changed_record_id` in the candidate role's inspected ownership; a context-code item
names one exact `context_record_id`. Requirement items name a trusted frozen source, and
validation items name an approved, actually run check with its expected status. Free-form
evidence strings are never sufficient input; human-readable details are flattened only
after record identities are bound.

Use `complete-graph` as the current all-success path, and invoke
`complete-legacy-graph` explicitly only for a pre-extension `2.0` bundle. Use
`advance-review` when outcomes arrive independently or any role may fail. The lifecycle
path validates before accepting content, records at most two attempts per role, preserves
incomplete pair state, and emits only pending main-agent candidates.

## Main-agent adjudication

For each candidate, verify the changed-scope cause, reachable path, typed anchor, checked
context, and material impact against the frozen evidence. Screen and deduplicate proposed
checks before running any. Merge duplicates by root cause, calibrate severity and
confidence, and discard unsupported claims. A single-reviewer candidate may become a final
finding after verification; reviewer consensus is not an evidence requirement. An
unresolved factual conflict becomes a question or verification gap, never a finding.

A `verified_finding` decision may carry a `calibration` object that replaces any evidenced
narrative or rating field: `title`, `changed_scope_causality`, `reachable_scenario`,
`impact`, `checked_context`, `severity`, `confidence`, or `correction_direction`. It cannot
replace finding identity, `changed_record_id`, or the frozen typed anchor. Calibration is
invalid on rejected, question, or verification-gap decisions, and finalization validates
severity, confidence, and non-empty text before applying it.

When reachability depends on an unchanged caller, guard, test, or type, pass its normalized
tracked path in `request.context_paths` before scope resolution. The workflow reads only the
regular blob at the resolved immutable revision, records its blob OID and SHA-256 content
identity in `frozen_context_records`, and binds those records into `scope_id`. Main-agent
verification can cite one such record with `kind: context_code` and its exact
`context_record_id`. A changed-record ID cannot stand in for unchanged caller or guard
evidence; if the relevant context cannot be frozen, report a verification gap.
Each frozen context record has the closed fields `context_record_id`, `source`, `revision`,
`path`, `mode`, `blob_oid`, and `content_identity`; IDs are unique, paths are normalized,
mode is regular, revisions and blob IDs are immutable Git OIDs, and content identities are
SHA-256 digests.
The before-dispatch and before-report scope gates compare the complete validated manifest,
including frozen context, trusted requirement sources, and candidate validation commands;
changing any evidence source invalidates prior reviewer results.

Do not dump internal contracts into the user report. For each finding, retain the exact
`changed_record_id` and render the complete typed anchor fields: line anchors include
`kind`, `side`, `path`, and `line`; typed change anchors include every available
`change_type`, `path`, `old_path`, `new_path`, `old_mode`, `new_mode`, `old_oid`, and
`new_oid` field. Then provide a concise coverage summary derived from the ledger. A pending
or invalid role, a mismatched scope ID, or any uncovered record must remain an explicit gap.
