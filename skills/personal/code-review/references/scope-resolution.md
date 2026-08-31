# Scope Resolution

Use this reference while resolving and freezing the review target. The bundled helper is
read-only: it emits one JSON Scope Manifest to stdout and diagnostics to stderr.
It requires a POSIX runtime and fails closed before repository containment or Git
inspection on unsupported platforms.

Resolve from the skill directory or use the helper's absolute path:

```bash
python3 scripts/review_scope.py local --repo /path/to/repository
python3 scripts/review_scope.py commit <revision> --repo /path/to/repository
python3 scripts/review_scope.py commit <merge-revision> --parent <number-or-oid> --repo /path/to/repository
python3 scripts/review_scope.py range '<left>..<right>' --repo /path/to/repository
python3 scripts/review_scope.py branch --base <base-ref> --head <head-ref> --repo /path/to/repository
python3 scripts/review_scope.py pr --number <number> --base <local-base-ref> --head <local-head-ref> --repo /path/to/repository
```

Quote explicit ranges so the shell cannot reinterpret them. The helper rejects invalid
refs, an ambiguous merge commit without `--parent`, and a selected object that is not an
actual merge parent. A root commit uses Git's empty-tree object. Two-dot ranges freeze the
two requested endpoints; three-dot ranges freeze both endpoints and their merge base.
Branch and pull-request modes always use merge-base semantics.
If Git reports more than one best merge base, the helper rejects the comparison as
ambiguous instead of selecting one silently.
PR numbers and untracked-byte limits must be positive integers in both CLI and direct API
use. Repository and context path text must be non-empty and contain no control characters;
invalid values return a controlled error instead of a traceback.

Repository discovery preserves the requested root boundary. A requested directory with
its own `.git` directory or gitfile must resolve to that exact canonical worktree, with
the same Git directory and common directory from both locations. This permits normal and
linked worktrees but rejects local `core.worktree` redirects to a sibling or ancestor.
Only an ordinary nested path without its own Git root marker may resolve upward to its
containing worktree.

An explicit PR request authorizes only the necessary read-only hosting metadata, ref, and
diff retrieval with existing authentication. Retrieve base/head identities first, make
them locally inspectable when authorized, then call `pr`. The helper itself performs no
network access. If local data cannot preserve the identified PR base/head and retrieval
is unavailable, report the gap instead of substituting another target. Ask before network
lookup for a generic review.

## Local content

Local mode records staged and unstaged tracked changes separately. It includes untracked
content only after confirming the final entry is a regular file and its physical resolved
path remains under the repository root. It never follows a final untracked symlink.
An index containing any unmerged stage is not a coherent local snapshot and is rejected
with the affected paths before staged or unstaged records are produced. Resolve or abort
the merge conflict, then rerun scope resolution.

Ignored, generated, vendored, likely-secret, oversized, symlink, FIFO, socket, device, and
otherwise special candidates are excluded with reasons. The default untracked size limit
is 1 MiB; use `--max-untracked-bytes` only when the user or trusted repository policy
establishes another proportionate limit. Do not override likely-secret or containment
exclusions without separate authorization and a safer inspection plan.

Local discovery sorts admitted entries deterministically and applies the same 100,000
worktree-entry budget as repository snapshot preflight. Traversal streams scanner entries:
every encountered entry consumes budget before stat, pruning, or retention, and discovery
fails on the first entry beyond the limit without materializing the remainder. An ignored,
generated, or vendored directory without tracked descendants is recorded once at its root
and is not descended; directories needed to reach tracked content remain traversable. A
budget overflow propagates as `snapshot_budget_exceeded`, pauses preflight before reviewer
dispatch, leaves pair coverage incomplete, and requires a smaller snapshot scope before
retry.

At every traversal depth, recognize a nested-repository boundary only from a structurally
valid Git administration layout: a regular `HEAD` plus `objects` and `refs` directories;
a linked layout with regular `HEAD`, `commondir`, and `gitdir`; or a regular `.git` gitfile
whose target has one of those layouts. Gitfile reads use a no-follow descriptor, are capped
at 4096 bytes, and revalidate identity; boundary detection never opens administrative
`config`. Empty, malformed, or symlinked `.git` markers and directories without a valid
layout remain ordinary traversable content.

Local scope emits one `nested-repository` exclusion for a validated boundary and does not
inspect or budget its descendants. Repository snapshotting retains the boundary root
metadata and any descendant explicitly owned by the outer repository's frozen index,
including the directory prefixes needed to reach it. Snapshotting derives each immediate
child name from those frozen index bytes and uses no-follow relative stat/open operations;
it never enumerates inner siblings. Inner-owned and untracked descendants are not inspected
or charged. The outer repository's own root `.git` entry is handled by the separate
Git-administration snapshot and is never worktree content.

Classify likely-secret untracked paths before opening or hashing them. The conservative
classifier covers common secret, password, auth, API-key, client-secret, credential,
token, keychain/keystore, and service-account data names; netrc/npm/PyPI auth files; and
the established AWS, Azure, GCloud, Kubernetes, and Docker credential locations. It also
excludes `.envrc`, `.envrc.*`, `.dev.vars`, `.dev.vars.*`, `*.tfvars`, and
`*.tfvars.json`, while explicit example templates remain reviewable. Apply
these name families only to credential artifacts and structured data extensions so
ordinary source such as `auth.py`, `tokenizer.py`, or credential helper modules remains
reviewable.

Tracked symlinks are represented from Git link blobs or `readlink` data after their parent
path is proven contained. Committed target blobs use UTF-8 `surrogateescape`, preserving
non-UTF-8 target bytes for exact round trips through the JSON string contract. Their
targets are never opened. Binary, file-mode, rename-only,
symlink, and gitlink records receive typed change anchors; text additions use new-side line
anchors and deletions use old-side anchors. Each tracked text diff hunk is a separate
changed record with an explicit old/new region, allowing distinct functional ownership
within one file. Untracked files and non-text changes remain whole typed records.

Treat every dirty tracked gitlink as an untrusted nested-repository boundary. Before
reading its HEAD or status, require its canonical worktree top level to equal the contained
gitlink path exactly and require both its absolute Git directory and common directory to
remain under the outer repository. Bind every unstaged gitlink identity to that contained
HEAD and status even when Git's raw diff retains the index OID. A redirected or otherwise
escaped nested worktree fails scope resolution without reading or hashing its status.

Hunk identity comes from a canonical patch: color and the indent heuristic are disabled,
inter-hunk context is zero, the Myers algorithm is fixed, output indicators are explicit
when the installed Git supports that interface, and rename detection uses a fixed candidate
limit. An incomplete exhaustive rename scan fails closed instead of silently changing record
shape. Edited renames are inspected with both old and new literal paths. Parse patch
structure by LF-delimited bytes, validate each hunk's old/new line counts, and decode only
textual fields; ambient Git formatting or diff configuration must not alter record
boundaries or anchors.

Force `core.fileMode=true` and `--ignore-submodules=none` for canonical comparisons so
repository-local config cannot hide executable-bit or gitlink changes. Generate canonical
patches as text, but determine binary status from NUL bytes in frozen Git blobs or a safely
opened contained worktree file. Do not let worktree `.gitattributes`, Git administrative
attributes, or an ambient attributes file decide whether a frozen record has hunks.

All Git inspection disables external diff programs, text conversion, lazy object fetching,
terminal credential prompts, and optional index locks. A missing local object is a coverage
gap; it never authorizes a fetch or helper execution.

The local `scope_id` hashes the target, included record metadata, staged object IDs,
unstaged content identities, safe untracked content identities, and exclusions. Re-run the
same command immediately before dispatch and reporting. A changed ID means the snapshot
drifted; discard affected results and do not claim complete coverage across identities.

## Manifest handling

The helper does not choose requirements or validation commands. Add only trusted
requirement sources and candidate commands established under the main skill's trust rules,
then validate the resulting manifest. Keep it ephemeral and do not write it into the
reviewed repository.
