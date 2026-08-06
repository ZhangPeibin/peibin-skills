---
name: to-pr
description: "Open a pull request from the current branch into a user-supplied base branch using committed changes."
disable-model-invocation: true
argument-hint: "<base-branch>"
---

# To-PR

Open a pull request from the current branch to the user-supplied base.

## Preflight
1. **Require the base branch.**
  - Read the base branch from `$ARGUMENTS`.
  - If no base was supplied, ask the user: "Which base branch should this PR target?" Wait for the answer before continuing.
  - Require exactly one branch name. Never detect, guess, or silently replace the supplied base.
  - Store the supplied value as `base` and treat it as untrusted input in every shell command.

2. **Verify the Git repository and `origin`.**
  - Run `git rev-parse --is-inside-work-tree` and `origin_url="$(git remote get-url origin)"`.
  - If either command fails, STOP and tell the user: "This skill must run inside a Git repository with an `origin` remote."
  - Run `git check-ref-format --branch "$base"`. If it fails, STOP and tell the user that the supplied base is not a valid Git branch name.

3. **Check if `gh` is installed.**
  - Run `gh --version`. If `gh` is missing, STOP and tell the user:
    "This skill needs gh. Install with `brew install gh` or follow the instructions at https://cli.github.com/ to install it."

4. **Determine the GitHub host and check authentication.**
  - Derive the hostname from `origin_url`. Support SCP-style SSH (`git@<host>:<owner>/<repo>.git`) and URI-style HTTPS/SSH remotes.
  - Do not fall back to `github.com`. If the hostname cannot be derived, STOP and report that `origin` is not a recognized GitHub remote; redact any embedded credentials before showing the URL.
  - Run `gh auth status --hostname "$host" --active`.
  - In Codex or another sandboxed agent environment, `gh` may be authenticated in the user's shell while the sandbox cannot read the macOS keyring. If the first check fails with a keyring, missing token, invalid token, or connection-looking error, rerun the same command with escalated permissions before deciding authentication is broken.
  - If the escalated check also fails, STOP and tell the user:
    "This skill requires `gh` authentication for <host>; run `gh auth login -h <host>` in your normal terminal, or provide `GH_TOKEN` / `GITHUB_TOKEN` to this agent session."
  - Never run `gh auth status --show-token`, and never print token values in the final response.

5. **Verify access to the GitHub repository.**
  - Run `gh repo view --json nameWithOwner,url` and confirm that the returned repository corresponds to `origin`.
  - If the command fails with an authentication, keyring, or network-looking error in a sandbox, rerun it with escalated permissions.
  - If access still fails or the returned repository does not match `origin`, STOP and report the exact failure without exposing credentials.

6. **Check the current branch.**
  - Run `current="$(git branch --show-current)"`.
  - If `current` is empty, STOP and tell the user: "HEAD is detached. Switch to or create a feature branch before opening a PR."
  - If `current` equals `base`, STOP and tell the user: "The head and base branches are both <branch>. Switch to a feature branch or choose a different base."
  - If `current` is `main`, `master`, or `pre`, STOP and tell the user that this skill does not open PRs from protected branches.

7. **Verify that the base exists on `origin`.**
  - Run `git ls-remote --exit-code --heads origin "$base"`.
  - If it returns no matching branch, STOP and tell the user that `origin/$base` does not exist.
  - Continue to **Process** only after every preflight check passes.
  
## Process
 1. **Inspect the committed changes.**
    - Run `git fetch origin`.
    - Run `git log --format='%h %s%n%b' "origin/$base..$current"`. STOP if no commits are returned.
    - Start with `git diff --stat "origin/$base...$current"`, then inspect the full diff or individual paths as needed. Continue only after understanding the role of every changed file; STOP if there are no changes.

2. **Draft the PR.**
    - Using the Process 1 findings, format the title as a Conventional Commit subject and complete [PULL_REQUEST_TEMPLATE.md](PULL_REQUEST_TEMPLATE.md).
    - Explain the changes' purpose and observable effect rather than merely restating the diff.
    - Present the title and body to the user for review.

3. **Create the PR.**
    - Wait for approval. Return to step 2 for requested revisions; STOP if the user declines.
    - Run `git push -u origin "$current"`.
    - Quote all variable expansions and pass the approved body through a single-quoted HEREDOC with `--body-file -`:
      ```sh
      gh pr create \
        --base "$base" \
        --head "$current" \
        --title "$title" \
        --body-file - <<'EOF'
      <approved PR body>
      EOF
      ```
    - Never use `--body` or an unquoted HEREDOC for generated content.
    - If the push or PR creation encounters a sandbox-related authentication or network failure, retry with escalated permissions.
    - Show the PR URL.

## Boundaries
1. **No fabrication.** The title and body may only contain what is evidenced by the commits and diff. Never invent motivation, and never claim tests that were not verified.
2. **No auto-merge.** After creating the PR, NEVER run `gh pr merge`. Merging is the decision of the reviewer and the user; this skill only opens the PR.
3. **No code changes.** Like `to-commits`, only read the changes and open the PR. Never edit files or add commits just to make the PR look better.
4. **base ≠ head.** Never create a PR where base and head are the same branch (it will fail), and never silently change the base the user confirmed.
5. **Push the current branch only.** `git push` pushes only the current feature branch. Never force-push, and never touch any other branch.
