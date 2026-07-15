---
name: to-commits
description: Turn the user's uncommitted changes into multiple well-scoped commits, grouped by logical concern.
disable-model-invocation: true 
---

# To-Commits

Split the current uncommitted changes into **several small**, **well-scoped**
Conventional Commits, each containing exactly one logical change.

When writing the commit message in process 4, follow the styles in [CONVENTIONAL-COMMIT-STYLE.md](CONVENTIONAL-COMMIT-STYLE.md). 

## Process
1. **See what changed.** Gather the full set of changes yourself: run `git status --porcelain` to list every modified, staged, and untracked file, then `git diff HEAD` for tracked content. Untracked (new) files won't appear in the diff, so treat anything `git status` lists as in scope. If you can't run git, ask the user to paste both outputs.
2. **Group the changes (by file).** Apply the **Grouping Rules** below to sort every changed file into a logical group. A file belongs to exactly one group, so each file lands in a single commit.
3. **Sort the Groups.** Sort by dependencies; commit code that is depended upon by other code first. 
    1. Core/Shared (types, utils, config)
    2. Business Logic
    3. API/Routing Layer
    4. Testing
    5. Chore/Docs
4. **Write commit message per group.** Using Conventional Commit style:

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```
5. **Present the plan.** Present the **final commit** plan to the user. Ask: "Does this plan look right? Proceed with committing?" 
   Wait for confirmation before continuing.

6. **Execute the plan.** For each group in order:
   1. `git add <files in this group>`
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

7. **Offer to push (optional).** Only after commits succeed, ask:
   "Push these commits to <remote>/<current-branch>? (y/n)"
   - Push only the current branch to its own remote tracking branch.
   - Never force-push. Never push to a different branch than the one
     checked out without explicit, specific confirmation.
   - If the user declines, stop here — the commits stay local.

8. **Offer to integrate (optional).** If the user wants these changes on
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
1. Only group changes into the same commit if they are causally related or part of the same functional change.

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
1. Commit locally without asking; anything that touches a **remote** or a **shared branch** (push, merge, PR) requires explicit, specific 
   confirmation naming the exact branch and target.
2. NEVER force-push (`push -f` / `--force-with-lease`), `reset --hard`, `rebase`, or `commit --amend` unless the user explicitly requests it.
3. NEVER push directly to a protected branch (main/master/dev/pre/prod)
  without a clear warning and confirmation; prefer a PR.
4. Do not commit suspected secrets (`.env`, `credentials.*`, keys). Warn
  and skip unless the user insists.
5. Pass multi-line commit messages via a HEREDOC, not chained `-m`.
6. **Never create, modify, or delete any file.** This includes CHANGELOG.md, README updates, or anything outside git's tracked state.
