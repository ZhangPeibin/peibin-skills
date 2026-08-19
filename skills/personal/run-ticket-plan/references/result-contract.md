# Ticket Execution Result Contract

The controller enforces this contract. Read it when diagnosing a contract failure or using a recovery primitive; normal public workflow instructions use compact controller output.

## Contents

1. Ticket and dependency contract
2. Tracker baseline contract
3. State and frontier contract
4. Planning Packet contract
5. Verification Plan contract
6. Verification Result contract
7. Post-completion external checkpoint contract
8. Roadmap contract
9. Command and recovery contract

## Ticket and dependency contract

Store tickets at `<feature-root>/issues/<NN>-<slug>.md`. Accept `00` and later zero-padded Core ids. Require one matching level-one heading, these metadata fields, and these sections:

```markdown
# 00 Ticket title

**Status:** Open
**Wave / Layer:** W0 / Bootstrap
**Owner Repo:** example-repo
**Depends On:** `00`, `01`, `03-05`, `57 PASS`, `E00-E14`, or `None`
**Unblocks:** `06`, `E01`, or `None`
**Core Completion Gates:** EXT-1, DEL-2

## Delivery
## Core Acceptance
## External Integration Evidence
## Invariants / Out Of Scope
## Evidence
## Comments
```

Treat numeric references as Core and `E`-prefixed references as External. Extract explicit backtick references from surrounding English or Chinese prose; also accept a bare field only when the complete value is a delimited reference list. Expand inclusive ranges and retain the `PASS` suffix as a condition. Do not infer references from ordinary years, amounts, or prose numbers. Require every Core dependency reference to exist; permit External references without local ticket files. Reject malformed ranges, duplicates, missing dependencies, self-dependencies, and cycles without emitting cascading missing-reference errors after a parse failure. Treat `Unblocks` as navigation only; it never creates a DAG edge.

The canonical spec owns product and architecture truth. After formal publication, each ticket's frozen Delivery, Core Acceptance, External Integration Evidence, Invariants / Out Of Scope, and original Evidence requirements are its complete execution contract. `next` verifies the canonical spec's published identity but does not reread its body to invent additional ticket requirements. Return an incomplete or contradictory ticket to the publication workflow. Preserve frozen fields and every unknown Markdown section verbatim while executing the ticket. `Core Completion Gates` is an optional, comma-delimited machine-readable list of requirement ids; do not infer gates from prose.

## Tracker baseline contract

Before `next`, `confirm`, `claim`, `reconcile`, `verify`, `complete`, or `record-external`, inspect the enclosing Git repository. Require the canonical spec, every formal Core ticket, and `.ticketctl.json` when present to be tracked and available at `HEAD`. The current canonical spec and config must equal their published versions. Compare each ticket's normalized frozen contract to `HEAD`: Wave / Layer, Owner Repo, parsed Depends On conditions, Unblocks, optional Core Completion Gates, Delivery, Core Acceptance, External Integration Evidence, Invariants / Out Of Scope, and original Evidence requirements must match. Parse frozen Evidence, Core completion receipts, and external anchors as independent channels. Exclude only exact controller receipt entries and structurally complete controller anchor entries in their reserved marker/channel. Preserve all ordinary requirement prose, including text that merely contains `Verification PASS` or starts with `External checkpoint`, whether inside or outside a marker, so keywords and markers cannot hide contract text. Ignore Status, Verification Plan, Verification Result, Comments, Reconciliation Record, External Checkpoint Result, External Checkpoint Anchor, and other execution-only sections.

Treat the roadmap as a fully derived execution view. Do not require it to be tracked or present at `HEAD`, report it as an unpublished tracker artifact, include it in source revision or completion integrity, or let it influence ticket status, dependencies, or frontier. This is an authority change, not a weaker frozen contract: formal tickets remain the sole execution-state authority and the roadmap is reproducible from them.

Return nonzero `TRACKER_BASELINE_MISSING` with separate `untracked_files`, `no_baseline_commit`, `missing_files`, `unpublished_spec_files`, `config_drift`, and `frozen_contract_drift` lists when the baseline is incomplete or unpublished. A source-only commit does not satisfy this gate. Report `NOT_A_GIT_REPOSITORY` when Git baseline enforcement is not applicable. Never create a commit.

## State and frontier contract

Permit only these transitions:

```text
Open -> In Progress -> Done
In Progress -> Blocked -> In Progress
In Progress -> REMEDIATE -> In Progress
Done -> In Progress (explicit reconcile only)
```

`REMEDIATE` is a Verification Result, not a ticket status. Keep a PASS ticket `In Progress` until `complete` enforces the completion gate. Set a BLOCKED verification to ticket status `Blocked`; use `resume` only after satisfying its recorded unlocking condition. `reconcile` is the only legal `Done -> In Progress` transition and is available only when historical completion evidence is legacy or invalid.

Define the frontier as Core tickets that are `Open`, owned by the current repository, unclaimed, structurally valid, and whose Core dependencies are satisfied. An unconditional Core dependency is satisfied by a `Done` ticket with valid modern historical integrity or legacy completion that is explicitly reported as migratable. Invalid modern historical integrity is fatal and blocks consumers. A `PASS` dependency additionally requires the latest Verification Result to be PASS and current for its source revision, plan hash, immutable contract hash, artifacts, and dependencies. Recheck these conditions during `complete`. External references do not create missing-file errors and do not independently block Core DAG calculation; their required evidence is enforced by the selected ticket's Verification Plan.

Permit only one local execution lane. `next` must refuse a fresh activation while any ticket is `In Progress` or `Blocked`. It may finish an interrupted activation only for the same `In Progress` ticket; require an explicit `resume` after satisfying a Blocked ticket's unlocking condition. `status` reports the unique active ticket or fatal multiple-lane drift without changing state.

Historical completion integrity proves that a ticket passed at its recorded revision. It validates only the Core plan/result/frozen-contract/receipt relationship; external records and anchors have a separate fault domain. It does not compare the recorded source fingerprint to the current ordinary working tree, rehash artifacts, or re-evaluate dependencies. Later implementation work or damaged external evidence therefore does not stale old Done tickets. It still detects a changed canonical spec, frozen ticket contract, plan, result, or Core receipt. Current-source validity is a separate release gate used by `NN PASS` and by `complete` for the selected ticket.

Keep five distinct concepts: the published frozen tracker baseline at `HEAD`; the current ordinary working-tree source revision; the historical Core completion result and receipt; an explicit current-source `PASS` dependency gate; and independent post-completion external handoff evidence. Never substitute one for another.

Report `STATE_DRIFT` without silently changing state when:

- an `Open` ticket has a Verification Result, meaningful Evidence, or an existing file/artifact named by its plan;
- a legacy `Done` ticket lacks a modern structured result (`migratable`);
- a `Done` ticket has attempted but inconsistent modern completion evidence (`fatal`);
- a post-completion external record or its Evidence anchor is missing, changed, conflicting, or bound to the wrong Core result (`informational`, without invalidating Core completion);
- a roadmap checkbox disagrees with ticket status;
- roadmap Current Frontier differs from the calculated frontier;
- completion writes `Done` but cannot synchronize the roadmap.

Use `fatal` for integrity or baseline failures that can block consumers, `migratable` for explicit workflow repairs such as legacy Done reconciliation or implemented Open tickets, and `informational` for derived roadmap drift. `status` reports drift and the independently calculated frontier together. Code or evidence existing is never equivalent to `Done`.

## Planning Packet contract

Use `prepare-next` only as the internal read-only first phase of public `next`. It performs the same baseline, fatal-drift, single-lane, frontier, dependency, interrupted-activation, Blocked, and existing-plan eligibility checks as `next`. It must not acquire a mutation lock, create or update a repository file, synchronize the roadmap, persist a packet, or use a cache.

Return one versioned compact JSON packet containing only the selected ticket identity and paths, status and plan state, dependency edges, completion gates, exact derived requirement ids/source/text, blocking obligations, complete plan-generation constraints, existing valid plan identity when present, compact baseline/spec/immutable-contract identities, calculated frontier, roadmap state, and nonfatal drift counts. Exclude the canonical spec body, raw ticket Markdown, other ticket contracts, full roadmap prose, detailed drift records, and product source. Packet size should scale with the selected ticket contract, not the tracker size.

The Planning Packet is a model input optimization, not durable authority and not a capability token. Generate it from current files on every invocation and emit it only on stdout. When `planning_required` is false, reuse the existing valid Plan. Otherwise treat packet requirements as the sole source of product requirements. Applicable repository instructions remain authoritative for operating constraints and may supply approved verification commands and paths, but they must not add or reinterpret product requirements. Do not load the complete canonical spec, tracker, roadmap, or this reference during normal planning. `next` must reload and independently revalidate the complete tracker under its exclusive mutation lock before persisting anything. Baseline, lane, dependency, frozen contract, or Plan changes after packet generation therefore fail closed without relying on packet freshness.

## Verification Plan contract

Establish the Verification Plan during `next`, before product implementation begins. The control-plane agent may read only the current Planning Packet and applicable `AGENTS.md` instructions to design this acceptance contract; it must not inspect product code, the complete spec, other tickets, or roadmap prose to choose implementation seams or prescribe coding strategy. Every verification command, path, or evidence basis must be grounded in those permitted inputs. If any blocking requirement lacks a non-invented safe check, report the agent-level `CONTRACT_NOT_PLANABLE` outcome and stop before invoking mutating `next`. Validate a newly generated plan before atomically persisting it with the `Open -> In Progress` transition; adopt an already valid Plan only under the existing-plan rules in the command contract. A temporary `--plan-file` is transport only; always delete it on success or failure, and keep the formal ticket as the sole durable plan copy.

Insert exactly one `## Verification Plan` before `## Comments` before implementation. Put one JSON object in a fenced `json` block:

```json
{
  "version": 1,
  "ticket": "00",
  "checks": [
    {
      "id": "AC-1",
      "source": "Core Acceptance",
      "requirement": "Static analysis passes",
      "kind": "command",
      "command": ["dart", "analyze"],
      "expected": "exit_code == 0",
      "blocking": true,
      "covers": ["DEL-1", "AC-1"]
    },
    {
      "id": "INV-1",
      "source": "Invariants / Out Of Scope",
      "requirement": "No production writes",
      "kind": "assertion",
      "command": ["python3", "tool/check_no_production_writes.py"],
      "expected": "exit_code == 0",
      "blocking": true,
      "covers": ["INV-1"]
    }
  ]
}
```

Use unique check ids and one of these kinds:

- `command`: run an argv array or a shell-like string without shell operators; require `expected: "exit_code == 0"` and PASS only on exit code zero.
- `file`: require a repository-contained path and record its hash.
- `assertion`: provide a local command, `evidence_path`, or stable PASS evidence.
- `artifact`: require a repository-contained output path and record its hash.
- `external`: require independently issued evidence; use `blocking: false` for EXT-only handoff checkpoints and `true` for Core gates; never execute an external write.
- `manual-review`: require independently issued evidence; use `blocking: false` for EXT-only handoff checkpoints and `true` for Core gates; never self-approve.

The controller derives requirement ids from non-empty units in all contract sections: `DEL-*` for Delivery, `AC-*` for Core Acceptance, `EXT-*` for External Integration Evidence, `INV-*` for Invariants / Out Of Scope, and `EVD-*` for Evidence. Every check's `source` and `requirement` must exactly equal the section name and frozen text of at least one requirement named by that check's `covers`; invented, paraphrased, truncated, or cross-section bindings are invalid. A check may cover multiple ids and implicitly covers a same-named requirement when `covers` is omitted. Every requirement must be covered. Every `AC-*`, `INV-*`, and id listed in `Core Completion Gates` must have at least one blocking check; non-blocking diagnostics never satisfy that obligation. Use `Core Completion Gates` to make a `DEL-*`, `EVD-*`, or `EXT-*` requirement part of Core completion.

Every check covering `EXT-*` must be `external` or `manual-review`. An EXT-only check must be non-blocking. It becomes blocking when the same check also covers `AC-*`, `INV-*`, or another declared Core gate, or when its own `EXT-*` id appears in `Core Completion Gates`. `plan-check` rejects missing or unknown mappings, assertion-based impersonation of external evidence, blocking EXT-only checkpoints, non-blocking Core gates, an empty plan, duplicate ids, unknown kinds, missing expected outcomes, and non-boolean blocking flags.

When changing a plan, retain all prior Verification Result attempts and record the reason in the ticket. Never delete a failed attempt.

For `external` and `manual-review`, attach evidence supplied by the independent issuer:

```json
"evidence": {
  "result": "PASS",
  "reference": "stable URL, artifact path, or approval id",
  "issuer": "independent reviewer or system"
}
```

Do not put secrets in a plan or evidence reference.

## Verification Result contract

`verify` appends attempts under `## Verification Result` before `## Comments`. Each attempt contains one fenced JSON object and a human-readable summary. The latest attempt controls completion; older failures remain as history.

```json
{
  "version": 1,
  "ticket": "00",
  "attempt": 1,
  "result": "PASS",
  "source_revision": "sha256:<working-tree-fingerprint>",
  "plan_hash": "<sha256>",
  "immutable_contract_hash": "<sha256>",
  "checks": [
    {
      "id": "AC-1",
      "kind": "command",
      "blocking": true,
      "result": "PASS",
      "exit_code": 0,
      "evidence": {
        "command": ["dart", "analyze"],
        "stdout_sha256": "<sha256>",
        "stderr_sha256": "<sha256>"
      }
    }
  ],
  "failed_checks": [],
  "blocked_by": [],
  "warnings": []
}
```

Calculate the overall result as follows:

- Return `BLOCKED` when a blocking check lacks an available environment or independently issued external/manual evidence.
- Otherwise return `REMEDIATE` when any blocking check fails.
- Otherwise return `PASS`; retain non-blocking failures in `warnings` and as pending external checkpoints.

Bind the result independently to a stable working-tree fingerprint (`source_revision`), the canonical JSON plan (`plan_hash`), and the immutable contract (`immutable_contract_hash`). Capture the real worktree source fingerprint before creating the verification snapshot and recalculate it after all checks and tracker reload. Require exact equality before building or persisting any Result; an added, removed, modified, or mode-changed real source path is treated only as a concurrent external change, returns `CONCURRENT_MODIFICATION` with both fingerprints, and writes no Result. The immutable contract binds the canonical spec and normalized frozen ticket contract, including original Evidence requirements while excluding only controller entries in their reserved channel. It excludes mutable Status, Comments, Verification Plan, Verification Result, Reconciliation Record, and External Checkpoint Result. Exclude individual tracker artifacts from the source fingerprint so persisting execution state does not stale itself; do not exclude the whole feature root. Before the selected ticket completes, a later source, spec, frozen ticket contract, or plan change invalidates its result. After completion, ordinary source changes do not invalidate historical integrity, but an explicit current-source `PASS` gate still does.

Run every command-backed check only from a disposable copy of the repository under an OS-enforced sandbox. Map repository-contained absolute argv paths into that copy, redirect home/cache/temp state outside it, deny network access, and permit writes only inside the disposable repository and runtime directories; never execute a verification command in the real repository root. If no supported secure sandbox is available, return `BLOCKED` without executing the command. Fingerprint the disposable source and tracker before and after each command. Any mutation there makes that check `BLOCKED`, contaminates the snapshot so later local checks cannot PASS, and is discarded with the snapshot. Do not attempt rollback in the real worktree.

Do not trust persisted summary fields. Recompute the overall result, `failed_checks`, `blocked_by`, and `warnings` from the check results and require the check id set, kind, blocking flag, and evidence schema to match the current plan. A command or command-backed assertion may PASS only with integer `exit_code: 0`, exact normalized safe argv, and valid stdout/stderr hashes. Apply repository-contained path/hash validation to path-backed assertions, `file`, and `artifact`, and rehash them for current-source completion. Require stable assertion evidence and independent string `reference`/`issuer` values where applicable. Historical integrity reuses the same semantic validator but deliberately omits current-source, current-artifact, and current-dependency comparisons.

Require every blocking result to PASS before completion. A non-blocking external/manual checkpoint may remain `BLOCKED`; preserve it in `warnings`, `status`, and completion output. Append a controller-owned receipt marker containing the source revision, plan hash, immutable contract hash, and passing check ids before changing ticket status to `Done`. Use `for checks: None.` when no check passed; continue accepting the existing backtick-id form. Validate the complete candidate ticket and its historical Core integrity before writing `Done`.

## Post-completion external checkpoint contract

Use `record-external` only for a `Done` ticket with valid historical Core completion and a check that was non-blocking `external` or `manual-review` and non-PASS in its persisted completion Result. The operator must explicitly provide `result: PASS`, a non-empty stable reference, and a non-empty independent issuer. The implementing agent cannot issue or self-approve that evidence.

Write one versioned JSON record under a `### <check-id>` boundary in `## External Checkpoint Result` with ticket/check identity, timestamp, controller version, reference, issuer, the canonical Core Verification Result hash, the completion receipt hash, and a self-hash. Add its matching marker-bound anchor under `## External Checkpoint Anchor` in the same candidate-validated atomic ticket write. Parse each heading-bounded record and each check anchor independently across all external section occurrences. One malformed JSON object, invalid record, or invalid anchor must not discard a different check's valid record. Continue reading valid Revision 3 anchors stored in Evidence, but do not write new external anchors there. Do not modify the original Verification Plan, Verification Result, source revision, plan hash, immutable contract hash, completion receipt, Status, roadmap, DAG, or frontier.

Identical evidence for the same check is idempotent. Reject conflicting evidence, unknown checks, blocking Core gates, ordinary checks, non-Done tickets, malformed evidence, invalid Core completion, tracker baseline drift, and existing external evidence with invalid hashes or bindings. Malformed, duplicated, missing, or unclosed external records/anchors do not prevent tracker loading or ordinary consumers; `status` reports them independently and reopens only affected checkpoints. Treat duplicate external Result or Anchor sections as external informational drift, not ticket structure or frozen-baseline failure. After reviewing the damage, the operator may run the exact reported `record-external ... --repair` command. Rebuild one canonical external Result section and Anchor section from the target evidence plus every other independently valid record; never drop an unrelated valid checkpoint. Valid historical Core completion remains unchanged. A malformed Core receipt remains fatal and blocks consumers.

## Roadmap contract

Treat the roadmap as optional. Resolve an explicit `--roadmap` first, then `.ticketctl.json` key `roadmap`; otherwise discover exactly one of `<feature-root>/roadmap.md` or `<feature-root>/implementation-roadmap.md`. Reject multiple candidates with `ROADMAP_AMBIGUOUS`; report roadmap absence without blocking ticket execution. Keep configured paths inside the feature root.

During `next`, create `<feature-root>/implementation-roadmap.md` when no configured or discovered roadmap exists. Generate exactly one heading execution card for every local Core ticket. Each card must link to its formal ticket and derive Function from Delivery, Verification from Core Acceptance, original Evidence, and the Verification Plan, plus exact Depends On and Unblocks metadata. Generate exactly one `## Current Frontier` section with the stable marker block. Validate the complete candidate before an absent-file CAS write. Repeated generation and synchronization must be byte-idempotent.

When present, require exactly one recognizable checkbox for every Core ticket and exactly one `## Current Frontier` section. Support list items and Markdown heading execution cards that begin with a ticket id:

```markdown
- [ ] `00` Ticket title
- [x] 01 Another title
#### [x] [`02` Heading card](issues/02-heading-card.md)
```

`sync-roadmap` changes only checkbox marks and the stable marker block inside Current Frontier. It preserves prose before/after the marker, links, requirement notes, unknown sections, and all other Markdown. On a markerless roadmap it replaces exactly one continuous legacy frontier list in place; it fails with `ROADMAP_MARKER_MISSING` rather than inserting a second list when the legacy region is absent or ambiguous. It writes:

```markdown
## Current Frontier

<!-- ticketctl:frontier:start -->
- `00`
- `01`
<!-- ticketctl:frontier:end -->
```

Fail without writing when the roadmap is ambiguous, incomplete, duplicated, or references unknown tickets. Derive checkbox and frontier values from formal tickets. Never update a ticket from roadmap state.

Treat missing or malformed roadmap state as informational and independently repairable. Synchronize a valid existing roadmap without replacing user-authored prose. Rebuild a malformed derived view only through explicit `sync-roadmap --regenerate`; never silently discard user edits. Tracked and untracked roadmaps have identical controller behavior.

## Command and recovery contract

The controller prints stable JSON to stdout, a short human summary to stderr, and exits nonzero for validation or transition failures.

```text
ticketctl.py validate --feature-root <path>
ticketctl.py status --feature-root <path>
ticketctl.py frontier --feature-root <path>
ticketctl.py prepare-next --feature-root <path> [--ticket 00]
ticketctl.py next --feature-root <path> [--ticket 00] [--plan-file <temporary-plan.json>]
ticketctl.py confirm --feature-root <path> [--ticket 00]
ticketctl.py claim --feature-root <path> --ticket 00
ticketctl.py resume --feature-root <path> --ticket 00
ticketctl.py reconcile --feature-root <path> --ticket 00 [--reason <text>]
ticketctl.py plan-check --feature-root <path> --ticket 00
ticketctl.py verify --feature-root <path> --ticket 00
ticketctl.py complete --feature-root <path> --ticket 00
ticketctl.py record-external --feature-root <path> --ticket 00 --check EXT-1 --result PASS --reference <reference> --issuer <issuer> [--repair]
ticketctl.py sync-roadmap --feature-root <path> [--regenerate]
```

Expose `status -> next -> confirm` as the primary control-plane workflow. Keep `reconcile` and `record-external` as explicit administrative actions. Keep `prepare-next` internal to `next`, `validate` and `frontier` diagnostic, and the remaining low-level commands as recovery or compatibility primitives. `status` and `prepare-next` are byte-read-only. Public `next` composes current packet generation, model-authored Plan creation when required, and the existing mutating `next` command. The mutating phase independently validates the baseline and single lane, selects the same eligible ticket, validates and persists the complete plan with In Progress, generates or synchronizes the roadmap, and emits a Work Packet. The Work Packet contains the feature/repository/spec/ticket paths, frozen contract fields, plan hash and check ids, roadmap path, Implementation Agent boundaries, and `run-ticket-plan confirm <ticket>` handoff command. `confirm` composes `verify` and `complete`: PASS completes, REMEDIATE retains In Progress with failed-check handoff, and BLOCKED persists the blocker and exact unlocking condition. Neither command implements or repairs product code.

Return `MIGRATION_REQUIRED` for removed `run` and `continue` modes and direct callers to `status`, `next`, or `confirm`. Keep the lower-level commands as explicit recovery and compatibility primitives.

Run plan commands as direct argv with `shell=False`. Reject shell executables/operators, interpreter inline-eval flags, `env` wrapping, Git global options and external-helper flags, destructive executables, mutating Git subcommands, publish/deploy/release operations, mutating `find` actions, and repository-escaping evidence paths. A command rejected by policy produces a blocking check result with an explicit unlocking condition; do not execute it indirectly. Keep production, real-funds, testnet writes, secrets, releases, and independent gates outside automatic verification.

`reconcile` requires an explicit operator command, a `Done` ticket whose historical integrity is not valid, and a passing tracker baseline. Its low-level compatibility interface continues to accept an omitted `--reason` and records the controller fallback, but the public Skill action requires a non-empty operator-supplied reason and passes it through unchanged. The controller atomically records the timestamp, controller version, reason, original status, and prior integrity under `## Reconciliation Record`, then changes only Status to `In Progress`. It does not create or repair a plan, run checks, synthesize PASS, or modify the frozen contract. Return the controller's `next` value as the recovery handoff and end the administrative action; invoke `plan-check`, `verify`, or `complete` only when separately requested.

Use an exclusive per-feature lock plus optimistic concurrency control for mutations. The lock coordinates controller processes but not users, IDEs, or other writers. Before rename, compare the current file with the exact content used to build the candidate and fail with `CONCURRENT_MODIFICATION` on mismatch. Long-running `verify` must snapshot source immediately before checks; reload after checks; require the same source fingerprint; recheck baseline, status, exact Plan, Result history, frozen contract, and dependencies; then merge its Result into the latest text so allowed Comments survive. Any source mismatch writes no Result. Validate controller-generated ticket/roadmap candidates before write. Write-fsync-rename guarantees atomic replacement but is not itself lost-update protection.

For a fresh `next` whose Open ticket has no plan, validate the supplied plan and write Status and Verification Plan in one candidate-validated CAS operation. If the Open ticket already has a valid plan, reuse it and atomically update only Status; accept a supplied plan only when it is identical. Reject an invalid existing plan without overwriting it. If an older claim left the same ticket In Progress without a plan, permit `next` to add only the missing valid plan and finish roadmap synchronization. Never overwrite a different plan or activate another ticket.

A ticket write and roadmap write cannot be one filesystem transaction. Therefore, after a verified ticket is atomically set to `Done`, retain that authoritative state if roadmap synchronization fails, exit nonzero with `STATE_DRIFT`, name the unsynchronized file, and instruct the operator to restore the write/format condition and rerun `sync-roadmap`. The repair command is idempotent.
