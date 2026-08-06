---
name: to-commits
description: Turn the user's uncommitted changes into one or more well-scoped Conventional Commits, grouped by logical concern.
disable-model-invocation: true
---

# To-Commits

Turn the current uncommitted changes into **one or more small**, **well-scoped**
Conventional Commits. Each commit contains exactly one logical change; do not
split a single logical change merely to create multiple commits.

When writing commit messages, follow [CONVENTIONAL-COMMIT-STYLE.md](CONVENTIONAL-COMMIT-STYLE.md).

## Process
1. **Inspect the full state.** Run `git status --porcelain=v1`, `git diff --cached`, and `git diff` to inspect staged and unstaged changes separately. Read every untracked text file before grouping it. Report untracked binary or large files separately and ask whether to include them. If there are no changes, report that there is nothing to commit and stop. If you cannot run Git, ask the user to paste the outputs and the untracked-file contents.
2. **Protect existing staging.** Treat existing staged content as user-owned. Never unstage, reset, or restage it. If the existing index already contains more than one planned group, or a file has both staged and unstaged changes that must be separated, stop and ask the user how to proceed. A staged group may be committed only when it exactly matches one confirmed plan group.
3. **Group the changes.** Apply the **Grouping Rules** below. A file normally belongs to exactly one group. If unrelated changes in one file would require separate commits, stop and ask rather than staging the whole file or changing the existing index.
4. **Sort the groups.** Sort by dependencies; commit code that is depended upon by other code first.
    1. Core/Shared (types, utils, config)
    2. Business Logic
    3. API/Routing Layer
    4. Testing
    5. Chore/Docs
5. **Write a commit message per group.** Use Conventional Commit style:

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```
6. **Present the plan.** State each group, its files, its staged/unstaged state, and its commit message. Call out skipped suspected secrets and untracked binary or large files. Ask: "Does this plan look right? Proceed with committing?" Wait for explicit confirmation before continuing.

7. **Execute the plan.** After the user confirms, commit each group in order:
   1. For a confirmed group that exactly matches pre-existing staged content, leave the index unchanged. Otherwise, stage only the planned files with `git add -- <files in this group>`.
   2. Commit. For a single-line message use `git commit -m "<message>"`.
      For a multi-line message use a HEREDOC:
      ```
      git commit -m "$(cat <<'EOF'
      <type>[optional scope]: <description>

      <body>
      EOF
      )"
      ```
   Repeat until all groups are committed.

8. **Offer to push (optional).** Only after commits succeed, ask:
   "Push these commits to <remote>/<current-branch>? (y/n)"
   - Push only the current branch to its own remote tracking branch.
   - Never force-push. Never push to a different branch than the one
     checked out without explicit, specific confirmation.
   - If the user declines, stop here — the commits stay local.

9. **Offer to integrate (optional).** If the user wants these changes on
   a shared branch (e.g. dev / pre / staging), ask which target, then
   PREFER opening a pull request:
   `gh pr create --base <target> --head <current-branch>`
   - Only if the user explicitly insists on a local merge, show the exact
     commands first and confirm again before running:
     `git switch <target> && git merge --no-ff <current-branch>`
   - Warn clearly before pushing to any protected branch
     (main/master/dev/pre/prod).
   - Never delete branches or force-push as part of this.


## Grouping Rules
1. Group changes into the same commit only when they are causally related or part of the same functional change.

**Good** — auth change triggered notification update, same commit:
```
feat(auth): add token expiry and notify user on session end

- internal/auth/token.go
- internal/notification/session.go
```

**Bad** — auth and payment have no connection, split them:
```
# commit 1
feat(auth): add token expiry
- internal/auth/token.go

# commit 2
feat(payment): add refund flow
- internal/payment/refund.go
```

## Boundaries
1. Create local commits only after the user explicitly confirms the final plan. Do not ask again for each individual commit after that confirmation. Anything that touches a **remote** or a **shared branch** (push, merge, PR) requires separate, explicit confirmation naming the exact branch and target.
2. NEVER force-push (`push -f` / `--force-with-lease`), `reset --hard`, `rebase`, or `commit --amend` unless the user explicitly requests it.
3. NEVER push directly to a protected branch (main/master/dev/pre/prod)
  without a clear warning and confirmation; prefer a PR.
4. Do not commit suspected secrets, whether identified by file name or content (`.env`, `credentials.*`, keys, tokens). Warn and skip unless the user explicitly insists.
5. Pass multi-line commit messages via a HEREDOC, not chained `-m`.
6. Do not create, modify, or delete working-tree files. Git metadata changes required by `git add` and `git commit` are permitted.
