# Scope Resolution

Use this reference while resolving and freezing the review target. The bundled helper is
read-only: it emits one JSON Scope Manifest to stdout and diagnostics to stderr.

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

Ignored, generated, vendored, likely-secret, oversized, symlink, FIFO, socket, device, and
otherwise special candidates are excluded with reasons. The default untracked size limit
is 1 MiB; use `--max-untracked-bytes` only when the user or trusted repository policy
establishes another proportionate limit. Do not override likely-secret or containment
exclusions without separate authorization and a safer inspection plan.

Tracked symlinks are represented from Git link blobs or `readlink` data after their parent
path is proven contained. Their targets are never opened. Binary, file-mode, rename-only,
symlink, and gitlink records receive typed change anchors; text additions use new-side line
anchors and deletions use old-side anchors. Each tracked text diff hunk is a separate
changed record with an explicit old/new region, allowing distinct functional ownership
within one file. Untracked files and non-text changes remain whole typed records.

Hunk identity comes from a canonical patch: color and the indent heuristic are disabled,
inter-hunk context is zero, the Myers algorithm is fixed, output indicators are explicit
when the installed Git supports that interface, and rename detection uses a fixed candidate
limit. An incomplete exhaustive rename scan fails closed instead of silently changing record
shape. Edited renames are inspected with both old and new literal paths. Parse patch
structure by LF-delimited bytes, validate each hunk's old/new line counts, and decode only
textual fields; ambient Git formatting or diff configuration must not alter record
boundaries or anchors.

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
