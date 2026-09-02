# Executable Quality Gates

Use these gates when changing the to-audit skill. They evaluate observable review
behavior; they are not a second production orchestrator and do not replace independent
real-agent evaluation.

## Standard check

Run the repository check from the repository root:

```bash
uv run --isolated --no-project --no-config --no-env-file --with PyYAML==6.0.2 -- python3 -I skills/personal/to-audit/scripts/run_release_check.py
```

This is the only supported standard-check command. `uv` creates an isolated environment
without reading a repository project, configuration file, or environment file. Its
isolated Python runs the digest-bound `scripts/run_release_check.py`, which validates the
resolved targets of the current interpreter and `/usr/bin/make` as non-writable regular
executables with trusted ownership. The launcher preserves the original absolute Python
entrypoint when it crosses the child-process boundary, so dependencies installed only in
the isolated uv or virtual environment remain available. It freezes both that entrypoint's
identity and the resolved executable target, then revalidates both immediately before and
after each named Make phase (`check-environment`, `check-interpreter`, `validate`, `test`,
and `release-gate`). A replacement during any phase invalidates the run before another
phase starts. Before Make starts, the launcher removes inherited `MAKEFLAGS`, `MFLAGS`,
`GNUMAKEFLAGS`, `MAKEFILES`, `VIRTUAL_ENV`, Python startup paths, and noninteractive shell
startup hooks and fixes the child `PATH` to operating-system directories. A direct
`make check` fails closed because a repository Makefile cannot prevent GNU Make from
processing inherited pre-parse inputs such as `MAKEFLAGS=--eval=...` or `MAKEFILES`.
The launcher, scope resolver, and workflow CLI require a POSIX runtime and return a
controlled error before parsing workflow input or inspecting repository paths elsewhere.

After that boundary, the Makefile fixes the check shell, shell flags, test roots, and
isolated runner instead of accepting environment or command-line replacements. It
requires the launcher-bound absolute Python path for every phase under a sanitized
`PATH`, `PYTHONHOME`, and `PYTHONPATH`. The path is carried as a quoted environment value,
not parsed or normalized as a Make path list, so a valid absolute interpreter path may
contain spaces. The Python supervisor rejects repository-owned entrypoints or resolved
targets before Make starts. The interpreter is checked again for a regular executable
target, trusted ownership, and non-writable mode.
The whitespace-path regression creates its copied interpreter from the launcher-bound
`sys.executable` and proves that copy can execute; it never substitutes an ambient
`python3` found through `PATH`.
Validation output, every test-runner invocation, grading, and gate reproduction each have
independent observable success markers. A no-op shell, Python, runner, or conditional
`python3` earlier on `PATH` therefore fails instead of skipping a phase.

The check discovers the to-audit contract suites, the executable v1 regression
catalog, the v2 controlled workflows, the deterministic Git/schema cases, and the
repository packaging validator. It then runs the versioned behavioral probes, derives
the rubric and safety results from their observed subprocess outcomes, and reproduces
that bound evidence through the release gate. Fixture repositories are temporary and
isolated. The workflow harness writes JSON and Markdown only to stdout and must not leave
review artifacts in the reviewed repository.

Repository tests and release probes use the digest-bound `scripts/run_unittest.py` under
Python isolated mode. The runner removes inherited Python path/home settings, inserts only
the explicit test root, and rejects test classes loaded from outside that root. A
repository-root `unittest.py` or ambient `PYTHONPATH` therefore cannot replace the standard
library or the frozen probe module and produce a false green.

Each v1 catalog case is data-driven: `context` owns its fixture definition, while
`expected.observations` and `forbidden.observations` are executable process, output, or
fixture predicates. The runner may use the fixture `kind` to select setup mechanics, but
must not replace catalog observations with reduced profile-specific assertion branches.
Each original `decisions`, `report`, and `behaviors` statement is bound to one or more
of those observations; every observation must participate in at least one binding.
`fixture.controlled_inputs` owns any synthetic reviewer candidates, main-agent decisions,
checks, or safety attempts used to exercise orchestration. Those injected values prove
contract preservation and reporting only; they do not prove that a model independently
discovered the finding, calibrated its severity, or made the decision.

## Evaluation seams

The primary black-box seam accepts versioned JSON on stdin and returns the complete
controlled workflow as JSON, including final Markdown and the Coverage Ledger:

```bash
python3 scripts/review_workflow.py run < workflow-input.json
```

Inputs identify a synthetic repository, review request and scope, environment
capabilities, semantic partition, controlled isolated-role outcomes, main-agent candidate
decisions, checks, and attempted unsafe actions. The harness uses the same scope,
preflight, lifecycle, and finalization contracts as the skill. It does not invoke a
model, provider, credential, network service, validation command, or mutation.

`request.context_paths` may name normalized unchanged tracked files needed to verify a
caller, guard, test, or type. The evaluator freezes each regular blob from the resolved
immutable revision, records its OID and content digest in `frozen_context_records`, and
binds that evidence into the scope identity. `context_code` verification evidence must
select exactly one of those records. A changed hunk cannot be relabeled as unchanged
caller or guard evidence.

The repository argument is canonicalized to its exact Git worktree before traversal.
The requested path must stay within the resolved root, and the Git-directory identity
and common-directory identity must agree when queried from both locations. When the
requested directory carries its own `.git` directory or gitfile, its canonical path must
be the resolved top level; this blocks sibling and ancestor `core.worktree` redirects
without rejecting a legitimate linked worktree. An ordinary nested path without a Git
root marker may still normalize to its containing worktree. Git inspection neutralizes
inherited hooks and filesystem monitors.
It runs with an environment allowlist and fixed system/global config boundaries, forces
file-mode and submodule diff semantics, and classifies binary blobs from frozen or
contained content rather than attributes. Repository-local config, worktree attributes,
and Git administrative attributes therefore cannot omit or retype scope records.
Repository snapshots include the Git administrative tree as well as worktree/index/ref
state. Regular worktree content is opened relative to stable contained directory
descriptors with no-follow and nonblocking flags; the opened identity and directory entry
are revalidated after the read. The worktree root and every distinct Git directory and
common directory are also bound through stable no-follow directory descriptors, including
their device, inode, mode, size, mtime, and ctime. Create-then-delete activity at any of
those roots therefore changes the before/after state even when the visible entry set is
restored. Snapshots bind file device, inode, ctime, size, and mode without reading likely
secrets, so same-size persistent secret or object writes fail the no-mutation check. A
changed final snapshot blocks paired and degraded Markdown finalization. Traversal is deterministically
sorted and bounded to 100,000 worktree entries and 100,000 Git-administrative entries.
Each scanner admits entries one at a time, increments its shared counter before stat or
retention, and fails on the first excess entry. It never materializes an over-limit
directory before enforcing the budget, and sorts only admitted bounded entries. Git
administration uses a descriptor-based depth-first stack, revalidates each child identity
against its parent entry, and closes every opened descriptor on success or failure.
Content hashing is bounded to 64 MiB and applies only to staged, unstaged, safe untracked,
and explicitly selected context content; clean tracked files retain Git/index/ref identity
and metadata coverage. Git-ignored, generated, and vendored trees are represented by their
root metadata and pruned unless they contain tracked paths. Entries encountered before a
prune still consume the same preflight worktree-entry budget. Local-scope discovery uses
the same sorted entry accounting and pruning rules, emits one exclusion for each pruned
root instead of enumerating its descendants, and propagates the same budget failure.
Nested boundaries require a no-follow structural Git layout check: regular `HEAD` with
`objects` and `refs`, linked-layout `HEAD` with `commondir` and `gitdir`, or a bounded,
identity-revalidated regular gitfile pointing to one of those layouts. Bare layouts are
recognized directly. Invalid or symlinked markers remain ordinary content, and boundary
detection never reads administrative `config`. Valid nested roots retain their root
metadata plus outer-index-owned descendants and required prefixes; inner-owned and
untracked descendants are neither inspected nor charged to outer budgets. Snapshotting
decodes exact child names from the frozen staged-index bytes, then uses no-follow relative
stat/open operations below the boundary without enumerating inner sibling names. Exceeding
any budget returns `preflight_paused` before dispatch, keeps the reviewer pair incomplete,
and records a `snapshot_budget_exceeded` verification gap. Worktree and content overflows require
`reduce_snapshot_scope_and_retry`; Git-administration overflow requires
`compact_git_administration_or_approve_larger_limit_and_retry`. Every controlled
reviewer attempt must attest a unique isolated context ID; retries must use a new ID.
Git is resolved once from the operating system's trusted command path, validated as a
non-group/world-writable regular executable owned by the system or current host user, and
invoked by absolute path. Git subprocesses receive that trusted command path rather than
the ambient `PATH`, so a repository-provided `git` wrapper cannot execute before the
initial mutation snapshot.
Reviewer validation proposals pass through the production screening contract, and a
controlled validation result can bind only to an approved focused command whose exact
argv occurs in the frozen base `.to-audit-validation.json`. Each frozen command record
contains `argv`, `requires_network`, `requires_credentials`, and `writes_repository`;
reviewer flags may increase those authoritative effects but cannot reduce them. Duplicate
argv records deduplicate only when all authoritative effects agree; conflicting records
invalidate the frozen policy in any ordering. User and host authorization
belong to the real agent's out-of-band control plane and cannot be represented by this
single-stdin evaluator seam; caller-supplied request, environment, or `trust_source`
labels are not authority. Rejected proposals are reported as not run.
Likewise, `trusted_lenses` on workflow stdin cannot establish installed/system/user trust.
Lens applicability is exercised through the trusted deterministic contract seam; a real
runtime must obtain lenses from an out-of-band installed catalog.

The controlled acceptance fixture for external-provider disclosure uses a separate CLI
capability channel:

```bash
python3 scripts/review_workflow.py run \
  --provider-consent /control-plane/provider-consent.json < workflow-input.json
```

The artifact must be outside the reviewed worktree, its absolute Git directory, and its
Git common directory, and contain exactly
`schema_version`, `capability`, `consent_id`, `repository`, and `provider`. It authorizes
only disclosure to the named provider for the canonical repository; the evaluator still
does not invoke that provider. Stdin request/environment consent labels remain blocked.
The loader resolves parent directories without following the final entry, opens with
no-follow and nonblocking flags, and accepts only a bounded, stable, single-link regular
file owned by the current user and not writable by group or others. Symlinks, hard links,
FIFOs, and other special files fail before content is read.
This is a procedural control-plane boundary for deterministic testing, not a cryptographic
identity or authorization claim.

The narrow deterministic seam remains the versioned JSON/CLI surface documented in
[contracts-v2.md](contracts-v2.md) and
[scope-resolution.md](scope-resolution.md). Its fixtures use temporary Git repositories
for object, path, typed-anchor, snapshot, ownership, budget, pair, and coverage behavior.

## Independent release grade

Run the versioned no-input probe evaluator and pass its complete output to the reproducing
gate:

```bash
python3 scripts/review_workflow.py grade > grade.json
python3 scripts/review_workflow.py gate < grade.json
```

`grade` requires `tests/independent_agent_evaluation.json`, then executes the frozen probe
manifest. The evaluation artifact is produced by a separate standard-agent context before
the check; it records producer/context, the reviewed implementation digest, rubric scores,
safety invariants, unresolved findings, an attestation, and one evidenced semantic
observation for every v1 catalog case. The launcher-driven Make phase only consumes and
verifies this artifact. It never creates, refreshes, or silently changes independent-agent
evidence.
The artifact must be a bounded, stable, single-link regular file at its canonical path and
must exactly match one regular stage-0 Git index blob. A staged new artifact is valid for
pre-commit assessment; an untracked, unstaged-modified, symlinked, or external substitute
is not release evidence.

The v1 semantic observations contain exactly `case_id`, `status`, and non-empty `evidence`.
Every catalog ID must occur once. A missing or failed case sets v1 compatibility to zero,
adds a High release finding, and blocks release. This independent layer owns semantic
discovery and severity quality; the deterministic v1 probe awards only compatibility and
testability credit for controlled orchestration behavior.

Every unresolved finding in that artifact contains exactly a supported `severity` and a
non-empty `title`; missing or additional fields fail closed.

The artifact boundary is procedural and version-controlled, not a claim of cryptographic
identity. It is deliberately outside the workflow JSON/runtime input channel and excluded
from the implementation digest to avoid a circular hash. Missing, malformed, or stale
agent evidence fails closed. Final category scores are the minimum of deterministic-probe
and agent scores, with the additional all-v1-semantic-cases pass condition; safety results
are logical AND; unresolved findings are the union.

The implementation digest binds the Makefile and every non-cache entry in the skill tree,
including permission bits and empty directories. The Makefile, the
`scripts/run_release_check.py` launcher, and every entry below the skill's `scripts/` and
`tests/` release-input trees must be nofollow regular files or directories; a live or
broken symbolic link there fails before import or probe scoring. The launcher rejects a
symlinked Makefile before starting Make, and the launcher-driven Make phase applies the
complete release-input check before its runner or workflow CLI can execute. Symbolic links
in nonexecuted instruction or asset locations bind only their target bytes and are never
dereferenced; unsupported special entries fail closed instead of being omitted or read.

The grade output binds the grader version, probe-manifest digest, independent-evaluation
digest, Git HEAD, and implementation digest. `gate` verifies those bindings, validates the
complete observations and derived verdict, reruns the probes, and rejects stale, missing,
malformed, or caller-authored scorecards. Both commands emit JSON and exit zero only for a
release-ready grade, one for a reproduced blocked grade, and two for invalid evidence.
Both `grade` and `gate` recompute the complete binding after their probe/reproduction phase
and immediately before emitting a verdict. Concurrent changes to HEAD, implementation
bytes, the probe manifest, or independent evidence invalidate the mixed snapshot. They
also compare the repository metadata snapshot across the phase, so changing and restoring
the same bytes still invalidates the run through inode/ctime state.

The fixed rubric is:

- correctness and evidence quality: 25 points;
- safety: 20 points;
- scalability: 20 points;
- extensibility: 15 points;
- v1 compatibility: 10 points; and
- testability: 10 points.

Release requires all of the following:

- at least 94/100 overall;
- no unresolved Critical or High finding; and
- 100 percent of the safety-critical invariants passing.

The safety-critical invariants are report-only behavior, exact scope, disclosure
controls, path containment, no mutation, complete primary ownership, reviewer isolation,
honest full-pair claims, snapshot consistency, and honest coverage gaps. Score cannot
compensate for a failed safety invariant.

The deterministic grader independently observes the executable workflow, Git boundaries,
command non-execution, repository snapshots, catalog outputs, orchestration states, and
contract behavior. Its frozen probes include worktree and Git-root identity restoration,
an actual copied Python entrypoint beneath a whitespace-containing path, compact bounded
local-scope pruning, structurally validated nested-repository ownership, streaming entry
budget enforcement, direct frozen-index traversal below nested boundaries, bounded
canonical contract schema versions, and structured budget-specific pause behavior.
Controlled reviewer
outcomes still do not substitute for real-agent
semantic feature discovery or finding-quality evaluation; that separate hardening review
remains an additional release activity.
