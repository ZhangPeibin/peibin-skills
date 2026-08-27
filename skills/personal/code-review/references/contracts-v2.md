# V2 Contracts: Localized Paired Review

Use this reference after scope resolution for the one-Review-Unit v2 baseline. All records
are JSON-compatible and use schema major version `2`. Missing required fields and another
major version fail validation; additive fields are retained or ignored safely.

The helper reads JSON from stdin and writes JSON to stdout:

```bash
python3 scripts/review_contract.py validate --kind scope_manifest
python3 scripts/review_contract.py build-one-unit --unit-id <stable-id> --feature-name '<name>'
python3 scripts/review_contract.py validate --kind reviewer_result
python3 scripts/review_contract.py complete-one-unit
```

Pass the Scope Manifest to `build-one-unit`. Pass an object containing `bundle` and
`reviewer_results` to `complete-one-unit`. Keep inputs and outputs in process memory or
agent messages, never in the reviewed repository.

## Contract ownership

- **Scope Manifest:** frozen target identity, immutable base/head or local fingerprint,
  typed changed records, exclusions, trusted requirement sources, and proposed validation
  commands.
- **Feature Graph:** one localized Feature, one executable Review Unit, and exactly one
  primary owner for every changed record. Context-only records do not alter ownership.
- **Review Plan:** frozen scope ID, core-policy version, the unit, preflight status, and one
  assignment for each required role.
- **Reviewer Task Envelope:** task, scope, unit, role, owned records, immutable core policy,
  output contract, and budget. The two envelopes have the same evidence and policy but
  distinct mandates. Never add peer conclusions or progress.
- **Reviewer Result:** task, scope, unit, role, status, inspected record IDs, candidate
  findings, supporting evidence, proposed checks, and unresolved assumptions.
- **Coverage Ledger:** every record's primary owner, both role states and attempts, and
  covered status. A record is covered only when both roles have valid completed results;
  the ledger is complete only when every record is covered.

## Reviewer result

A successful reviewer returns `status: "completed"` and all owned record IDs in
`inspected_record_ids`. It may inspect additional context, but must not claim that context
as owned coverage. Every candidate finding contains:

- `finding_id` and concise `title`;
- a line or typed change `anchor` from the Scope Manifest;
- `changed_scope_causality`;
- `reachable_scenario` and `impact`;
- `checked_context`, including relevant guards, callers, tests, or types;
- `severity` and `confidence`; and
- `correction_direction`.

Return an empty candidate array when nothing survives the reviewer's own scrutiny. Put
uncertain product decisions in `unresolved_assumptions` and safe, focused commands in
`proposed_checks`; reviewers do not execute those checks independently.

Validate each result before accepting it. `complete-one-unit` additionally confirms that
task, role, unit, scope ID, and inspected ownership match the frozen plan and that both
roles completed. Candidate anchors must exactly match an owned typed anchor from the frozen
Scope Manifest. It returns candidates for main-agent adjudication, not final findings.

## Main-agent adjudication

For each candidate, verify the changed-scope cause, reachable path, typed anchor, checked
context, and material impact against the frozen evidence. Screen and deduplicate proposed
checks before running any. Merge duplicates by root cause, calibrate severity and
confidence, and discard unsupported claims. A single-reviewer candidate may become a final
finding after verification; reviewer consensus is not an evidence requirement.

Do not serialize internal contracts into the user report. Report findings first, then a
concise coverage summary derived from the ledger. A pending or invalid role, a mismatched
scope ID, or any uncovered record must remain an explicit gap.
